"""Interactive inference takes priority between bounded archive batches."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from interlinked_ai_node import LocalNode


class SchedulingTests(unittest.TestCase):
    def test_pending_chat_precedes_next_background_batch(self):
        with tempfile.TemporaryDirectory() as folder:
            node = LocalNode(Path(folder), {'computer': 'SYNTHETIC', 'sid': 'test'})
            started, release = threading.Event(), threading.Event()
            order, errors = [], []
            def request(method, path, body=None, *args, **kwargs):
                if path == '/api/tags':
                    return {'models': [{'name': 'embeddinggemma:300m', 'digest': 'a'*64}, {'name': 'writer', 'digest': 'b'*64}]}
                if path == '/api/embed':
                    order.append(body['input'][0])
                    if body['input'] == ['first']:
                        started.set(); self.assertTrue(release.wait(5))
                    return {'embeddings': [[1.0, 0.0]]}
                order.append('chat')
                return {'message': {'content': 'local'}, 'done': True}
            node._request = request
            def invoke(fn):
                try: fn()
                except Exception as exc: errors.append(exc)
            first = threading.Thread(target=invoke, args=(lambda: node.embed('embeddinggemma:300m', ['first'], 'a'*64, background=True),))
            chat = threading.Thread(target=invoke, args=(lambda: node.chat('writer', [{'role': 'user', 'content': 'fixture'}]),))
            second = threading.Thread(target=invoke, args=(lambda: node.embed('embeddinggemma:300m', ['second'], 'a'*64, background=True),))
            try:
                first.start(); self.assertTrue(started.wait(3)); chat.start()
                deadline=time.monotonic()+3
                while not node._interactive_waiters and time.monotonic()<deadline: time.sleep(.01)
                self.assertEqual(node._interactive_waiters, 1)
                second.start(); release.set()
                for thread in (first, chat, second): thread.join(5); self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(order, ['first', 'chat', 'second'])
            finally:
                release.set(); node.close()


if __name__ == '__main__': unittest.main()
