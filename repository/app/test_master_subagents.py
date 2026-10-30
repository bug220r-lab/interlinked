"""Synthetic metadata-only subagent counter checks; no service, USB or files."""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import time
import unittest

SPEC = importlib.util.spec_from_file_location('master_subagent_subject', Path(__file__).with_name('interlinked_master.py'))
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


class SubagentTests(unittest.TestCase):
    def setUp(self):
        self.model = m.EventModel()
        self.base = time.time() - 100
        self.root = str(Path(__file__).resolve().parent / 'synthetic-subagent-project')
        self.other = str(Path(__file__).resolve().parent / 'synthetic-subagent-other')
        self.registrations = [dict(id='one', root=self.root)]
        self.sequence = 0

    def event(self, kind='SubagentStart', agent='a', session='parent', second=1, cwd=None, turn='turn1'):
        self.sequence += 1
        value = dict(format='interlinked-master-event-v1', id='event' + str(self.sequence), event=kind,
                     sessionId=session, cwd=cwd or self.root,
                     observedAt=datetime.fromtimestamp(self.base + second, timezone.utc).isoformat())
        if agent is not None:
            value['agentId'] = agent
        if turn is not None:
            value['turnId'] = turn
        return value

    def accept(self, *args, **kwargs):
        value = self.event(*args, **kwargs)
        self.model.accept(value)
        return value

    def counts(self, second=10, registrations=None):
        return self.model.subagent_counts(registrations or self.registrations, self.base + second)

    def test_main_chats_and_generic_agent_ids_do_not_establish_child_role(self):
        self.accept('UserPromptSubmit', agent=None)
        self.accept('PreToolUse', agent='unclassified')
        projects, total = self.counts()
        self.assertIsNone(total['workingCount'])
        self.assertEqual(projects['one']['status'], 'unavailable')

    def test_unique_children_and_duplicate_delivery(self):
        value = self.accept(agent='a')
        self.assertFalse(self.model.accept(value))
        self.accept(agent='b')
        self.assertEqual(self.counts()[1]['workingCount'], 2)

    def test_generic_updates_preserve_role_and_waiting_is_excluded(self):
        self.accept()
        self.accept('PermissionRequest', second=2)
        self.assertEqual(self.counts()[1]['workingCount'], 0)
        self.accept('PostToolUse', second=3)
        self.assertEqual(self.counts()[1]['workingCount'], 1)

    def test_unique_child_session_hooks_update_the_explicit_child(self):
        self.accept()
        self.accept('PermissionRequest', agent=None, session='a', second=2)
        self.assertEqual(self.counts()[1]['workingCount'], 0)
        self.accept('PostToolUse', agent=None, session='a', second=3)
        self.assertEqual(self.counts()[1]['workingCount'], 1)
        self.accept('Stop', agent=None, session='a', second=4)
        self.assertEqual((self.counts()[1]['workingCount'], self.counts()[1]['status']), (0, 'observed'))

    def test_child_session_observation_before_role_discovery_is_preserved(self):
        self.accept('PermissionRequest', agent=None, session='a', second=3)
        self.accept('SubagentStart', second=1)
        self.assertEqual(self.counts()[1]['workingCount'], 0)
        self.assertEqual(self.model.subagents[('parent', 'a')]['state'], 'waiting')

    def test_delayed_role_uses_waiting_and_terminal_priority_across_hook_shapes(self):
        for kind in ('PermissionRequest', 'Stop'):
            for generic_first in (False, True):
                with self.subTest(kind=kind, generic_first=generic_first):
                    self.model = m.EventModel()
                    explicit = self.event('PreToolUse', second=3)
                    generic = self.event(kind, agent=None, session='a', second=3)
                    for value in (generic, explicit) if generic_first else (explicit, generic):
                        self.model.accept(value)
                    self.accept('SubagentStart', second=1)
                    self.assertEqual(self.counts()[1]['workingCount'], 0)
                    self.assertEqual(self.model.subagents[('parent', 'a')]['state'], 'waiting' if kind == 'PermissionRequest' else 'stopped')

    def test_ambiguous_child_session_hook_does_not_choose_a_parent(self):
        self.accept(session='parent')
        self.accept(session='parent2')
        self.accept('PermissionRequest', agent=None, session='a', second=3)
        self.assertIsNone(self.counts()[1]['workingCount'])
        self.accept('PostToolUse', session='parent', second=4)
        self.assertEqual((self.counts()[1]['workingCount'], self.counts()[1]['status']), (1, 'partial'))

    def test_late_duplicate_relationship_invalidates_prior_child_session_inference(self):
        self.accept(session='parent', second=1)
        self.accept('Stop', agent=None, session='a', second=3)
        self.assertEqual(self.counts()[1]['workingCount'], 0)
        self.accept(session='parent2', second=2)
        self.assertIsNone(self.counts()[1]['workingCount'])
        self.accept('SubagentStop', session='parent', second=4)
        self.assertEqual((self.counts()[1]['workingCount'], self.counts()[1]['status']), (0, 'partial'))

    def test_ambiguous_child_session_turn_ids_cannot_establish_a_fresh_zero(self):
        for parent in ('parent', 'parent2'):
            self.accept(session=parent, second=1)
            self.accept('SubagentStop', session=parent, second=2)
        # Inferred child turns do not share a proven namespace with the
        # parent's turn. Either spelling leaves resumed work uncertain.
        self.accept('PreToolUse', agent=None, session='a', second=3)
        self.assertIsNone(self.counts()[1]['workingCount'])
        self.accept('PreToolUse', agent=None, session='a', second=4, turn='turn2')
        self.assertIsNone(self.counts()[1]['workingCount'])
        self.accept('SubagentStop', session='parent', second=5)
        self.assertEqual((self.counts()[1]['workingCount'], self.counts()[1]['status']), (0, 'partial'))

    def test_child_session_directory_does_not_replace_initiating_project(self):
        self.accept()
        self.accept('PostToolUse', agent=None, session='a', second=2, cwd=self.other)
        projects, _ = self.counts(registrations=self.registrations + [dict(id='two', root=self.other)])
        self.assertEqual(projects['one']['workingCount'], 1)
        self.assertIsNone(projects['two']['workingCount'])

    def test_expired_confirmed_stop_does_not_taint_fresh_child_evidence(self):
        self.accept(agent='old', second=1)
        self.accept('SubagentStop', agent='old', second=2)
        self.accept(agent='new', second=90)
        self.assertEqual((self.counts(second=190)[1]['workingCount'], self.counts(second=190)[1]['status']), (1, 'observed'))
        self.assertIsNone(self.counts(second=300)[1]['workingCount'])

    def test_stop_has_observed_zero_and_late_start_cannot_revive_it(self):
        start = self.event(second=1)
        self.accept('SubagentStop', second=2)
        self.model.accept(start)
        self.assertEqual(self.counts()[1]['workingCount'], 0)
        self.assertEqual(self.counts()[1]['status'], 'observed')

    def test_same_turn_generic_update_cannot_revive_terminal(self):
        self.accept()
        self.accept('SubagentStop', second=2)
        self.accept('PreToolUse', second=3)
        self.assertEqual(self.counts()[1]['workingCount'], 0)

    def test_explicit_later_start_reuses_child_without_turn_id(self):
        self.accept(turn=None)
        self.accept('SubagentStop', second=2, turn=None)
        self.accept('SubagentStart', second=3, turn=None)
        self.assertEqual(self.counts()[1]['workingCount'], 1)
        self.assertEqual(len(self.model.subagents), 1)

    def test_new_turn_activity_reuses_child(self):
        self.accept()
        self.accept('SubagentStop', second=2)
        self.accept('PreToolUse', second=3, turn='turn2')
        self.assertEqual(self.counts()[1]['workingCount'], 1)

    def test_late_role_discovery_uses_newer_waiting_update(self):
        self.accept('PermissionRequest', second=3)
        self.accept('SubagentStart', second=1)
        self.assertEqual(self.counts()[1]['workingCount'], 0)
        self.assertEqual(self.counts()[1]['status'], 'observed')

    def test_equal_time_waiting_wins_over_start_in_both_delivery_orders(self):
        for waiting_first in (False, True):
            with self.subTest(waiting_first=waiting_first):
                self.model = m.EventModel()
                kinds = ('PermissionRequest', 'SubagentStart') if waiting_first else ('SubagentStart', 'PermissionRequest')
                for kind in kinds:
                    self.accept(kind, second=1)
                self.assertEqual(self.counts()[1]['workingCount'], 0)
                self.assertEqual(self.model.subagents[('parent', 'a')]['state'], 'waiting')
                self.accept('PostToolUse', second=2)
                self.assertEqual(self.counts()[1]['workingCount'], 1)

    def test_equal_time_terminal_wins_over_new_turn_start_and_waiting(self):
        for kinds in (('SubagentStop', 'SubagentStart'), ('SubagentStart', 'SubagentStop'),
                      ('PermissionRequest', 'SubagentStop'), ('SubagentStop', 'PermissionRequest')):
            with self.subTest(kinds=kinds):
                self.model = m.EventModel()
                for index, kind in enumerate(kinds):
                    self.accept(kind, second=1, turn='turn' + str(index))
                self.assertEqual(self.counts()[1]['workingCount'], 0)
                self.assertEqual(self.model.subagents[('parent', 'a')]['state'], 'stopped')
                self.accept('SubagentStart', second=2, turn=None)
                self.assertEqual(self.counts()[1]['workingCount'], 1)

    def test_unattributed_permission_makes_only_its_parent_uncertain(self):
        self.accept(agent='a')
        self.accept(agent='b', session='other-parent')
        self.accept('PermissionRequest', agent=None, second=2)
        self.assertEqual(self.counts()[1]['workingCount'], 1)
        self.assertEqual(self.counts()[1]['status'], 'partial')
        self.accept('PostToolUse', second=3)
        self.assertEqual(self.counts()[1]['workingCount'], 2)
        self.assertEqual(self.counts()[1]['status'], 'observed')

    def test_main_activity_does_not_refresh_or_resolve_children(self):
        self.accept()
        self.accept('PermissionRequest', agent=None, second=2)
        self.accept('PostToolUse', agent=None, second=4)
        self.assertIsNone(self.counts()[1]['workingCount'])
        self.accept('PostToolUse', second=5)
        self.accept('PostToolUse', agent=None, second=99)
        self.assertIsNone(self.counts(second=186)[1]['workingCount'])

    def test_parent_permission_delivered_before_older_child_start_is_uncertain(self):
        self.accept('PermissionRequest', agent=None, second=3)
        self.accept('PostToolUse', agent=None, second=4)
        self.accept('SubagentStart', second=1)
        self.assertIsNone(self.counts()[1]['workingCount'])
        self.accept('SubagentStop', second=5)
        self.assertEqual(self.counts()[1]['workingCount'], 0)

    def test_child_started_after_parent_permission_is_working(self):
        self.accept('PermissionRequest', agent=None, second=1)
        self.accept('SubagentStart', second=2)
        self.assertEqual(self.counts()[1]['workingCount'], 1)

    def test_parent_uncertainty_memory_is_bounded(self):
        for i in range(257):
            self.accept('PermissionRequest', agent=None, session='parent' + str(i))
        self.assertEqual(len(self.model.subagent_parent_uncertainty), 256)
        self.assertTrue(self.model.subagent_coverage_lost)

    def test_unknown_and_stale_children_degrade_known_work(self):
        self.accept(agent='old', second=1)
        self.accept(agent='new', second=90)
        total = self.counts(second=182)[1]
        self.assertEqual((total['workingCount'], total['status']), (1, 'partial'))
        self.assertEqual(self.counts(second=300)[1]['status'], 'unavailable')

    def test_future_observation_is_not_current(self):
        self.accept(second=20)
        self.assertIsNone(self.counts(second=10)[1]['workingCount'])

    def test_restart_does_not_inherit_running_count(self):
        self.accept()
        restarted = m.EventModel()
        self.assertIsNone(restarted.subagent_counts(self.registrations)[1]['workingCount'])

    def test_count_is_not_truncated_to_activity_rows(self):
        for i in range(40):
            self.accept(agent='child' + str(i))
        self.assertEqual(len(self.model.activity([self.root], self.base + 10)['agents']), 32)
        self.assertEqual(self.counts()[1]['workingCount'], 40)

    def test_capacity_eviction_marks_incomplete_coverage(self):
        for i in range(257):
            self.accept(agent='child' + str(i))
        total = self.counts()[1]
        self.assertEqual((total['workingCount'], total['status']), (256, 'partial'))
        self.assertEqual(len(self.model.subagents), 256)

    def test_missing_child_identity_cannot_fabricate_count(self):
        self.accept()
        self.accept('SubagentStop', agent=None, second=2)
        total = self.counts()[1]
        self.assertEqual((total['workingCount'], total['status']), (1, 'partial'))

    def test_explicit_feed_error_degrades_existing_count(self):
        self.accept()
        self.model.subagent_coverage_lost = True
        self.assertEqual(self.counts()[1]['status'], 'partial')

    def test_parent_project_precedes_child_working_directory(self):
        self.accept('UserPromptSubmit', agent=None, cwd=self.root)
        self.accept(cwd=self.other)
        registrations = self.registrations + [dict(id='two', root=self.other)]
        projects, total = self.counts(registrations=registrations)
        self.assertEqual(projects['one']['workingCount'], 1)
        self.assertIsNone(projects['two']['workingCount'])
        self.assertEqual((total['workingCount'], total['status']), (1, 'partial'))

    def test_late_parent_event_supplies_initiating_project(self):
        self.accept(cwd=self.other, second=2)
        self.accept('UserPromptSubmit', agent=None, cwd=self.root, second=1)
        registrations = self.registrations + [dict(id='two', root=self.other)]
        projects, total = self.counts(registrations=registrations)
        self.assertEqual(projects['one']['workingCount'], 1)
        self.assertIsNone(projects['two']['workingCount'])
        self.assertEqual(total['workingCount'], 1)

    def test_nested_children_inherit_explicit_ancestor_project(self):
        self.accept(agent='child')
        self.accept(agent='nested', session='child', cwd=self.other)
        registrations = self.registrations + [dict(id='two', root=self.other)]
        projects, total = self.counts(registrations=registrations)
        self.assertEqual(projects['one']['workingCount'], 2)
        self.assertIsNone(projects['two']['workingCount'])
        self.assertEqual(total['workingCount'], 2)

    def test_overlapping_roots_assign_once_to_most_specific_match(self):
        nested = str(Path(self.root) / 'nested')
        self.accept(cwd=nested)
        registrations = self.registrations + [dict(id='two', root=nested)]
        projects, total = self.counts(registrations=registrations)
        self.assertIsNone(projects['one']['workingCount'])
        self.assertEqual(projects['two']['workingCount'], 1)
        self.assertEqual(total['workingCount'], 1)

    def test_equal_roots_use_registration_order(self):
        self.accept()
        registrations = self.registrations + [dict(id='two', root=self.root)]
        projects, _ = self.counts(registrations=registrations)
        self.assertEqual(projects['one']['workingCount'], 1)
        self.assertIsNone(projects['two']['workingCount'])

    def test_same_child_label_under_two_parents_is_two_identities(self):
        self.accept(session='parent')
        self.accept(session='parent2')
        self.assertEqual(self.counts()[1]['workingCount'], 2)

    def test_old_stopped_history_does_not_taint_fresh_work(self):
        self.base -= 200
        self.accept(agent='old', second=1)
        self.accept('SubagentStop', agent='old', second=2)
        self.accept(agent='working', second=190)
        summary = self.counts(second=200)[1]
        self.assertEqual((summary['workingCount'], summary['status']), (1, 'observed'))
        self.model.subagent_coverage_lost = True
        self.assertEqual(self.counts(second=200)[1]['status'], 'partial')


    def test_all_old_stopped_history_does_not_establish_fresh_zero(self):
        self.accept('SubagentStop', second=1)
        summary = self.counts(second=200)[1]
        self.assertIsNone(summary['workingCount'])
        self.assertEqual(summary['status'], 'unavailable')


    def test_child_session_hooks_update_work_wait_stop_and_keep_generic_activity(self):
        self.accept(agent='child', second=1)
        self.accept('PermissionRequest', agent=None, session='child', second=2, turn='child-turn')
        self.assertEqual(self.counts()[1]['workingCount'], 0)
        self.assertEqual(self.model.subagents[('parent', 'child')]['state'], 'waiting')
        self.assertEqual(self.model.sessions[('child', 'main')]['sessionId'], 'child')
        self.assertNotIn('agentId', self.model.sessions[('child', 'main')])
        self.accept('PostToolUse', agent=None, session='child', second=3, turn='child-turn')
        self.assertEqual(self.counts()[1]['workingCount'], 1)
        self.accept('Stop', agent=None, session='child', second=4, turn='child-turn')
        self.assertEqual(self.counts()[1]['workingCount'], 0)
        self.accept('PreToolUse', agent=None, session='child', second=5, turn='different-child-turn')
        self.assertIsNone(self.counts()[1]['workingCount'])
        self.assertEqual(self.model.subagents[('parent', 'child')]['state'], 'stopped')
        self.accept('SubagentStart', agent='child', second=6, turn='new-parent-turn')
        self.accept('PreToolUse', agent=None, session='child', second=7, turn='new-child-turn')
        self.assertEqual(self.counts()[1]['workingCount'], 1)


    def test_child_session_activity_refreshes_only_its_explicit_child(self):
        self.base -= 200
        self.accept(agent='child', second=1)
        self.accept(agent='sibling', second=2)
        self.accept('PreToolUse', agent=None, session='child', second=190, turn='child-turn')
        summary = self.counts(second=200)[1]
        self.assertEqual((summary['workingCount'], summary['status']), (1, 'partial'))
        self.assertEqual(self.model.subagents[('parent', 'sibling')]['observedAt'],
                         datetime.fromtimestamp(self.base + 2, timezone.utc).isoformat())


    def test_ambiguous_child_session_stop_does_not_choose_a_parent(self):
        self.accept(agent='child', session='parent1', second=1)
        self.accept(agent='child', session='parent2', second=1)
        self.accept('Stop', agent=None, session='child', second=2, turn='child-turn')
        self.assertIsNone(self.counts()[1]['workingCount'])
        self.assertTrue(all(child['state'] == 'working' for child in self.model.subagents.values()))
        self.accept('PostToolUse', agent='child', session='parent1', second=3)
        summary = self.counts()[1]
        self.assertEqual((summary['workingCount'], summary['status']), (1, 'partial'))


    def test_delayed_start_uses_newer_generic_child_session_evidence_and_original_owner(self):
        self.accept('UserPromptSubmit', agent=None, session='parent', second=1, cwd=self.root)
        self.accept('PermissionRequest', agent=None, session='child', second=3, turn='child-turn', cwd=self.other)
        self.accept('SubagentStart', agent='child', second=2, cwd=self.root)
        child = self.model.subagents[('parent', 'child')]
        self.assertEqual(child['state'], 'waiting')
        self.assertEqual(child['cwd'], self.root)
        projects, _ = self.counts(registrations=self.registrations + [dict(id='two', root=self.other)])
        self.assertEqual(projects['one']['workingCount'], 0)
        self.assertIsNone(projects['two']['workingCount'])


    def test_delayed_stop_cannot_use_generic_child_session_work_to_revive_it(self):
        self.accept('PreToolUse', agent=None, session='child', second=3, turn='child-turn')
        self.accept('SubagentStop', agent='child', second=2, turn='parent-turn')
        self.assertIsNone(self.counts()[1]['workingCount'])
        self.assertEqual(self.model.subagents[('parent', 'child')]['state'], 'stopped')


    def test_inferred_activity_after_explicit_stop_makes_count_uncertain(self):
        for kind in ('UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'PermissionRequest'):
            with self.subTest(kind=kind):
                self.model = m.EventModel()
                self.accept('SubagentStart', agent='child', second=1, turn='parent-turn')
                self.accept('SubagentStop', agent='child', second=2, turn='parent-turn')
                self.accept(kind, agent=None, session='child', second=3, turn='child-turn')
                summary = self.counts()[1]
                self.assertIsNone(summary['workingCount'])
                self.assertEqual(summary['status'], 'unavailable')
                self.assertTrue(self.model.subagents[('parent', 'child')]['uncertainAt'])


    def test_inferred_activity_after_child_session_stop_does_not_keep_observed_zero(self):
        self.accept('SubagentStart', agent='child', second=1)
        self.accept('Stop', agent=None, session='child', second=2, turn='child-turn1')
        self.accept('UserPromptSubmit', agent=None, session='child', second=3, turn='child-turn2')
        self.assertIsNone(self.counts()[1]['workingCount'])


    def test_old_stopped_child_with_later_inferred_activity_taints_fresh_other_work(self):
        self.base -= 200
        self.accept('SubagentStop', agent='old', second=1)
        self.accept('PreToolUse', agent=None, session='old', second=190, turn='child-turn')
        self.accept(agent='working', second=191)
        summary = self.counts(second=200)[1]
        self.assertEqual((summary['workingCount'], summary['status']), (1, 'partial'))


    def test_ambiguous_inferred_resume_cannot_keep_stopped_children_confident(self):
        self.accept('SubagentStop', agent='child', session='parent1', second=1)
        self.accept('SubagentStop', agent='child', session='parent2', second=1)
        self.accept('PreToolUse', agent=None, session='child', second=2, turn='child-turn')
        self.assertIsNone(self.counts()[1]['workingCount'])


    def test_equal_time_inferred_activity_does_not_undermine_confirmed_stop(self):
        self.accept('SubagentStop', agent='child', second=1)
        self.accept('PreToolUse', agent=None, session='child', second=1, turn='child-turn')
        self.assertEqual(self.counts()[1]['workingCount'], 0)


    def test_delayed_second_relationship_invalidates_ambiguous_session_evidence(self):
        self.accept('Stop', agent=None, session='child', second=3, turn='child-turn')
        self.accept('SubagentStart', agent='child', session='parent1', second=1)
        self.assertEqual(self.counts()[1]['workingCount'], 0)
        self.accept('SubagentStart', agent='child', session='parent2', second=2)
        self.assertIsNone(self.counts()[1]['workingCount'])


    def test_nested_parent_child_session_permission_does_not_mark_descendants_waiting(self):
        self.accept(agent='child', second=1)
        self.accept(agent='nested', session='child', second=2)
        self.accept('PermissionRequest', agent=None, session='child', second=3, turn='child-turn')
        self.assertEqual(self.counts()[1]['workingCount'], 1)
        self.assertEqual(self.model.subagents[('parent', 'child')]['state'], 'waiting')
        nested = self.model.subagents[('child', 'nested')]
        self.assertEqual(nested['state'], 'working')
        self.assertFalse(nested.get('uncertainAt'))


    def test_unrelated_main_session_does_not_establish_or_refresh_child_role(self):
        self.base -= 200
        self.accept(agent='child', second=1)
        self.accept('PreToolUse', agent=None, session='unrelated', second=190)
        self.assertIsNone(self.counts(second=200)[1]['workingCount'])
        self.assertEqual(len(self.model.subagents), 1)


    def test_delayed_role_uses_terminal_over_equal_time_generic_work(self):
        self.accept('PreToolUse', agent='child', second=3)
        self.accept('Stop', agent=None, session='child', second=3, turn='child-turn')
        self.accept('SubagentStart', agent='child', second=1)
        self.assertEqual(self.counts()[1]['workingCount'], 0)
        self.assertEqual(self.model.subagents[('parent', 'child')]['state'], 'stopped')


    def test_future_terminal_evidence_remains_unresolved(self):
        self.accept('SubagentStop', agent='future', second=20)
        self.accept(agent='working', second=1)
        summary = self.counts(second=10)[1]
        self.assertEqual((summary['workingCount'], summary['status']), (1, 'partial'))


    def test_summary_contains_no_paths_or_identities(self):
        self.accept(agent='private-agent', session='private-session')
        projects, total = self.counts()
        for summary in (projects['one'], total):
            self.assertEqual(set(summary), {'workingCount', 'status', 'observedAt', 'source'})
            self.assertNotIn('private-agent', str(summary))
            self.assertNotIn(self.root, str(summary))


if __name__ == '__main__':
    unittest.main(verbosity=2)
