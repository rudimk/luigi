# -*- coding: utf-8 -*-
#
# Copyright 2012-2015 Spotify AB
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import random
from unittest import TestCase

import luigi
from luigi import Event, Task, build
from luigi.mock import MockFile, MockFileSystem
from luigi.task import flatten
from mock import patch


class DummyException(Exception):
    pass


class EmptyTask(Task):
    fail = luigi.BoolParameter()

    def run(self):
        if self.fail:
            raise DummyException()


class TaskWithCallback(Task):

    def run(self):
        print("Triggering event")
        self.trigger_event("foo event")


class TestEventCallbacks(TestCase):

    def test_start_handler(self):
        saved_tasks = []

        @EmptyTask.event_handler(Event.START)
        def save_task(task):
            print("Saving task...")
            saved_tasks.append(task)

        t = EmptyTask(True)
        build([t], local_scheduler=True)
        self.assertEqual(saved_tasks, [t])

    def test_success_handler(self):
        saved_tasks = []

        @EmptyTask.event_handler(Event.SUCCESS)
        def save_task(task):
            print("Saving task...")
            saved_tasks.append(task)

        t = EmptyTask(False)
        build([t], local_scheduler=True)
        self.assertEqual(saved_tasks, [t])

    def test_failure_handler(self):
        saved_tasks = []
        exceptions = []

        @EmptyTask.event_handler(Event.FAILURE)
        def save_task(task, exception):
            print("Saving task and exception...")
            saved_tasks.append(task)
            exceptions.append(exception)

        t = EmptyTask(True)
        build([t], local_scheduler=True)
        self.assertEqual(saved_tasks, [t])
        self.assertTrue(isinstance(exceptions[0], DummyException))

    def test_custom_handler(self):
        dummies = []

        @TaskWithCallback.event_handler("foo event")
        def story_dummy():
            dummies.append("foo")

        t = TaskWithCallback()
        build([t], local_scheduler=True)
        self.assertEqual(dummies[0], "foo")

    def test_processing_time_handler(self):
        @EmptyTask.event_handler(Event.PROCESSING_TIME)
        def save_task(task, processing_time):
            self.result = task, processing_time

        times = [43.0, 1.0]
        t = EmptyTask(random.choice([True, False]))
        with patch('luigi.worker.time') as mock:
            mock.time = times.pop
            build([t], local_scheduler=True)
        self.assertTrue(self.result[0] is t)
        self.assertEqual(self.result[1], 42.0)


#        A
#      /   \
#    B(1)  B(2)
#     |     |
#    C(1)  C(2)
#     |  \  |  \
#    D(1)  D(2)  D(3)

def eval_contents(f):
    with f.open('r') as i:
        return eval(i.read())


class ConsistentMockOutput(object):

    '''
    Computes output location and contents from the task and its parameters. Rids us of writing ad-hoc boilerplate output() et al.
    '''
    param = luigi.IntParameter(default=1)

    def output(self):
        return MockFile('/%s/%u' % (self.__class__.__name__, self.param))

    def produce_output(self):
        with self.output().open('w') as o:
            o.write(repr([self.task_id] + sorted([eval_contents(i) for i in flatten(self.input())])))


class HappyTestFriend(ConsistentMockOutput, luigi.Task):

    '''
    Does trivial "work", outputting the list of inputs. Results in a convenient lispy comparable.
    '''

    def run(self):
        self.produce_output()


class D(ConsistentMockOutput, luigi.ExternalTask):
    pass


class C(HappyTestFriend):

    def requires(self):
        return [D(self.param), D(self.param + 1)]


class B(HappyTestFriend):

    def requires(self):
        return C(self.param)


class A(HappyTestFriend):

    def requires(self):
        return [B(1), B(2)]


class TestDependencyEvents(TestCase):

    def tearDown(self):
        MockFileSystem().remove('')

    def _run_test(self, task, expected_events):
        actual_events = {}

        # yucky to create separate callbacks; would be nicer if the callback received an instance of a subclass of Event, so one callback could accumulate all types
        @luigi.Task.event_handler(Event.DEPENDENCY_DISCOVERED)
        def callback_dependency_discovered(*args):
            actual_events.setdefault(Event.DEPENDENCY_DISCOVERED, set()).add(tuple(map(lambda t: t.task_id, args)))

        @luigi.Task.event_handler(Event.DEPENDENCY_MISSING)
        def callback_dependency_missing(*args):
            actual_events.setdefault(Event.DEPENDENCY_MISSING, set()).add(tuple(map(lambda t: t.task_id, args)))

        @luigi.Task.event_handler(Event.DEPENDENCY_PRESENT)
        def callback_dependency_present(*args):
            actual_events.setdefault(Event.DEPENDENCY_PRESENT, set()).add(tuple(map(lambda t: t.task_id, args)))

        build([task], local_scheduler=True)
        self.assertEqual(actual_events, expected_events)

    def test_incomplete_dag(self):
        for param in range(1, 3):
            D(param).produce_output()
        self._run_test(A(), {
            'event.core.dependency.discovered': set([
                ('A(param=1)', 'B(param=1)'),
                ('A(param=1)', 'B(param=2)'),
                ('B(param=1)', 'C(param=1)'),
                ('B(param=2)', 'C(param=2)'),
                ('C(param=1)', 'D(param=1)'),
                ('C(param=1)', 'D(param=2)'),
                ('C(param=2)', 'D(param=2)'),
                ('C(param=2)', 'D(param=3)'),
            ]),
            'event.core.dependency.missing': set([
                ('D(param=3)',),
            ]),
            'event.core.dependency.present': set([
                ('D(param=1)',),
                ('D(param=2)',),
            ]),
        })
        self.assertFalse(A().output().exists())

    def test_complete_dag(self):
        for param in range(1, 4):
            D(param).produce_output()
        self._run_test(A(), {
            'event.core.dependency.discovered': set([
                ('A(param=1)', 'B(param=1)'),
                ('A(param=1)', 'B(param=2)'),
                ('B(param=1)', 'C(param=1)'),
                ('B(param=2)', 'C(param=2)'),
                ('C(param=1)', 'D(param=1)'),
                ('C(param=1)', 'D(param=2)'),
                ('C(param=2)', 'D(param=2)'),
                ('C(param=2)', 'D(param=3)'),
            ]),
            'event.core.dependency.present': set([
                ('D(param=1)',),
                ('D(param=2)',),
                ('D(param=3)',),
            ]),
        })
        self.assertEqual(eval_contents(A().output()), ['A(param=1)', ['B(param=1)', ['C(param=1)', ['D(param=1)'], ['D(param=2)']]], ['B(param=2)', ['C(param=2)', ['D(param=2)'], ['D(param=3)']]]])
