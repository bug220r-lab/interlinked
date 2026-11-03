"""Isolated launcher logging/prompt checks. No processes, network or kit state."""
from collections import deque
import importlib.util
import io
from pathlib import Path
import queue
import sys
import threading
import unittest
from types import SimpleNamespace

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('launcher_logging_gui',HERE/'interlinked_gui.py')
g=importlib.util.module_from_spec(spec);sys.modules[spec.name]=g;spec.loader.exec_module(g)


class Log(io.StringIO):
    def __init__(self,fail=None):
        super().__init__();self.writes=[];self.flushes=0;self.fail=fail;self.flushed=threading.Event()
    def write(self,text):
        self.writes.append(text)
        if self.fail=='write':raise OSError('synthetic full log')
        return super().write(text)
    def flush(self):
        self.flushes+=1
        if self.fail=='flush':raise ValueError('synthetic closed log')
        super().flush();self.flushed.set()


class Pipe:
    def __init__(self):self.values=queue.Queue()
    def feed(self,text):
        for character in text:self.values.put(character)
    def end(self):self.values.put('')
    def read(self,size):
        assert size==1
        return self.values.get(timeout=3)


def controller():
    value=SimpleNamespace(lock=threading.RLock(),output=deque(maxlen=16000),
        job=dict(id='current',action='open',status='RUNNING',awaitingDone=False,promptGeneration=0))
    value.append=lambda text:g.Controller.append(value,text)
    return value


class LoggingTests(unittest.TestCase):
    def setUp(self):
        self.flusher_names={t.ident for t in threading.enumerate() if t.name.startswith('INTERLINKED ')}
    def tearDown(self):
        # Every _read owns and joins its flusher, including on failures/EOF.
        self.assertEqual(self.flusher_names,{t.ident for t in threading.enumerate() if t.name.startswith('INTERLINKED ')})
    def read(self,value,text,label='stdout',log=None,job_id='current'):
        log=Log() if log is None else log
        g.Controller._read(value,io.StringIO(text),label,log,job_id)
        return log
    def run_reader(self,value,pipe,log,label='stdout',job_id='current'):
        errors=[]
        def work():
            try:g.Controller._read(value,pipe,label,log,job_id)
            except BaseException as error:errors.append(error)
        thread=threading.Thread(target=work)
        thread.start()
        return thread,errors
    def finish_reader(self,thread,errors):
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive(),'reader/flusher did not finish')
        self.assertFalse(errors,errors)

    def test_batched_unicode_log_and_bounded_ui_tail(self):
        value=controller();text=('\N{SNOWMAN}\nabcdef'*8192)+g.PROMPT
        log=self.read(value,text)
        self.assertEqual(log.getvalue(),text)
        self.assertLess(len(log.writes),100)
        self.assertEqual(log.flushes,len(log.writes))
        self.assertLessEqual(max(map(len,log.writes)),g.LOG_BATCH_CHARS)
        self.assertEqual(''.join(value.output),text[-16000:])
        self.assertTrue(value.job['awaitingDone']);self.assertEqual(value.job['promptGeneration'],1)

    def test_prompt_crosses_every_batch_boundary(self):
        for offset in range(1,len(g.PROMPT)):
            with self.subTest(offset=offset):
                value=controller();text='x'*(g.LOG_BATCH_CHARS-offset)+g.PROMPT
                log=self.read(value,text)
                self.assertEqual(log.getvalue(),text)
                self.assertTrue(value.job['awaitingDone']);self.assertEqual(value.job['promptGeneration'],1)

    def test_stderr_never_arms_prompt(self):
        value=controller();log=self.read(value,g.PROMPT*2,'stderr')
        self.assertEqual(log.getvalue(),g.PROMPT*2)
        self.assertFalse(value.job['awaitingDone']);self.assertEqual(value.job['promptGeneration'],0)

    def test_repeated_stdout_prompts_retain_generation(self):
        value=controller();self.read(value,g.PROMPT+'not closed yet\n'+g.PROMPT)
        self.assertEqual(value.job['promptGeneration'],2)

    def test_partial_output_flushes_while_stream_waits(self):
        value=controller();pipe=Pipe();log=Log();thread,errors=self.run_reader(value,pipe,log)
        try:
            pipe.feed('partial line without newline')
            self.assertTrue(log.flushed.wait(2),'partial log waited for newline/EOF')
            self.assertEqual(log.getvalue(),'partial line without newline')
            self.assertTrue(thread.is_alive(),'fixture must still be waiting for stream output')
            pipe.feed(g.PROMPT)
        finally:pipe.end();self.finish_reader(thread,errors)
        self.assertTrue(value.job['awaitingDone'])

    def test_log_failures_leave_prompt_and_output_active(self):
        for mode in ('write','flush'):
            with self.subTest(mode=mode):
                value=controller();text='x'*g.LOG_BATCH_CHARS+g.PROMPT;log=Log(mode)
                self.read(value,text,log=log)
                self.assertEqual(len(log.writes),1)
                self.assertIn('Output log failed',value.job['reportingWarning'])
                self.assertTrue(value.job['awaitingDone']);self.assertEqual(value.job['promptGeneration'],1)
                self.assertEqual(''.join(value.output),text)

    def test_stale_reader_keeps_own_log_without_changing_current_job(self):
        for mode in (None,'write','flush'):
            with self.subTest(mode=mode):
                value=controller();log=Log(mode);self.read(value,g.PROMPT,log=log,job_id='previous')
                self.assertFalse(value.job['awaitingDone']);self.assertEqual(value.job['promptGeneration'],0)
                self.assertNotIn('reportingWarning',value.job);self.assertFalse(value.output)
                if mode is None:self.assertEqual(log.getvalue(),g.PROMPT)

    def test_read_failure_drains_buffer_and_joins_flusher(self):
        class BrokenRead(io.StringIO):
            def read(self,size):
                text=super().read(size)
                if not text:raise OSError('synthetic broken pipe')
                return text
        value=controller();log=Log()
        with self.assertRaisesRegex(OSError,'synthetic broken pipe'):
            g.Controller._read(value,BrokenRead('before read error'),'stdout',log,'current')
        self.assertEqual(log.getvalue(),'before read error');self.assertEqual(log.flushes,1)

    def test_prompt_is_published_before_slow_prompt_flush(self):
        class SlowLog(Log):
            def __init__(self):super().__init__();self.entered=threading.Event();self.release=threading.Event()
            def flush(self):
                self.entered.set()
                if not self.release.wait(2):raise OSError('fixture sink was not released')
                return super().flush()
        value=controller();pipe=Pipe();log=SlowLog();thread,errors=self.run_reader(value,pipe,log)
        try:
            pipe.feed(g.PROMPT)
            self.assertTrue(log.entered.wait(2))
            with value.lock:self.assertTrue(value.job['awaitingDone'])
        finally:log.release.set();pipe.end();self.finish_reader(thread,errors)

    def test_independent_stdout_stderr_streams(self):
        value=controller();out=Pipe();err=Pipe();outlog=Log();errlog=Log()
        readers=[self.run_reader(value,out,outlog),self.run_reader(value,err,errlog,'stderr')]
        outtext='stdout unicode \N{GREEK SMALL LETTER PI}\n'+g.PROMPT;errtext='stderr warning\n'+g.PROMPT
        try:out.feed(outtext);err.feed(errtext)
        finally:
            out.end();err.end()
            for thread,errors in readers:self.finish_reader(thread,errors)
        self.assertEqual(outlog.getvalue(),outtext);self.assertEqual(errlog.getvalue(),errtext)
        self.assertEqual(value.job['promptGeneration'],1)

    def test_empty_stream_does_not_write_or_flush(self):
        log=self.read(controller(),'')
        self.assertFalse(log.writes);self.assertEqual(log.flushes,0)


if __name__=='__main__':unittest.main()
