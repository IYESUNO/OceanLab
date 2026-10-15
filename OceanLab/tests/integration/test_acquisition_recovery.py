from __future__ import annotations

import multiprocessing
import os
import threading
import time
import unittest
from functools import partial

from oceanlab.adapters.isolated_spectrometer import IsolatedSpectrometer
from oceanlab.adapters.simulation import SimulatedSpectrometer
from oceanlab.application.acquisition import AcquisitionService, CoalescingEventQueue
from oceanlab.domain.models import AcquisitionRequest, AcquisitionState
from oceanlab.ports.spectrometer import SpectrometerInterrupted
from tests.integration.test_device_pipeline import FakeDevice, make_adapter
from oceanlab.adapters.oceandirect import OceanDirectSpectrometer


class ControlledSpectrometer(SimulatedSpectrometer):
    def __init__(self, block_method, blocked, entered):
        super().__init__()
        self.block_method = block_method
        self.blocked = blocked
        self.entered = entered

    def initialize(self):
        return (1, 0, 0)

    def _wait(self, method):
        if self.block_method == method and self.blocked.is_set():
            self.entered.set()
            while self.blocked.is_set():
                time.sleep(0.01)

    def connect(self):
        self._wait("connect")
        return super().connect()

    def acquire(self, context):
        if self.block_method == "crash":
            os._exit(7)
        self._wait("acquire")
        return super().acquire(context)

    def apply_configuration(self, request):
        self._wait("apply_configuration")
        return super().apply_configuration(request)


class ScanningDevice(FakeDevice):
    def __init__(self, blocked, entered, scans):
        super().__init__()
        self.blocked, self.entered, self.scans = blocked, entered, scans

    def get_formatted_spectrum(self):
        with self.scans.get_lock():
            self.scans.value += 1
        self.entered.set()
        while self.blocked.is_set():
            time.sleep(0.005)
        return super().get_formatted_spectrum()


class ScanningSpectrometer(OceanDirectSpectrometer):
    """Exercise the real SDK adapter averaging in the real device process."""
    def __init__(self, blocked, entered, scans):
        self.__dict__.update(make_adapter(ScanningDevice(blocked, entered, scans)).__dict__)

    def initialize(self):
        return (1, 0, 0)

    def connect(self):
        return self.connected

    def check_connection(self):
        return True

    def shutdown(self):
        pass


class AcquisitionRecoveryTest(unittest.TestCase):
    def test_real_average_loop_pauses_at_scan_boundary_and_reuses_connection(self):
        for action in ("configure_single", "configure_start"):
            with self.subTest(action=action):
                mp = multiprocessing.get_context("spawn")
                blocked, entered, scans = mp.Event(), mp.Event(), mp.Value("i", 0)
                blocked.set()
                events = CoalescingEventQueue()
                adapter = IsolatedSpectrometer(partial(ScanningSpectrometer, blocked, entered, scans), 3.0)
                service = AcquisitionService(lambda: adapter, events)
                service.start()
                try:
                    service.send("connect")
                    self.wait_until(lambda: service.connected is not None)
                    process = adapter._process
                    service.send(action, integration_us=3_000_000, averages=20, boxcar=0,
                                 request_id="batch")
                    self.assertTrue(entered.wait(3.0))
                    service.send("stop", request_id="batch")
                    self.assertTrue(adapter._pause_requested.is_set())
                    blocked.clear()
                    self.wait_until(lambda: self.pause_acknowledged(events, "batch"))
                    self.assertEqual(scans.value, 1)
                    self.assertIs(adapter._process, process)
                    self.assertIsNotNone(service.connected)
                    self.assertFalse(any(event.kind == "spectrum" for event in events._items))
                    self.assertEqual(service.context.averages, 20)
                    service.send("configure_single", request_id="next")
                    self.wait_until(lambda: any(event.kind == "spectrum" for event in events._items))
                    self.assertEqual(scans.value, 21)
                    self.assertIs(adapter._process, process)
                    frame = next(event for event in events._items if event.kind == "spectrum")
                    self.assertEqual(frame.payload["context"].averages, 20)
                finally:
                    blocked.clear()
                    service.send("shutdown")
                    service.join(3.0)
                    adapter.shutdown()

    def make_isolated(self, method):
        context = multiprocessing.get_context("spawn")
        blocked, entered = context.Event(), context.Event()
        blocked.set()
        adapter = IsolatedSpectrometer(partial(ControlledSpectrometer, method, blocked, entered), 3.0)
        self.addCleanup(adapter.shutdown)
        return adapter, blocked, entered

    @staticmethod
    def wait_until(predicate, timeout=3.0):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            time.sleep(0.005)
        if not predicate():
            raise AssertionError("operation did not complete before test deadline")

    @staticmethod
    def pause_acknowledged(events, request_id):
        with events._lock:
            return any(event.kind == 'acquisition_state' and not event.payload['running']
                       and event.payload.get('cancelled') and event.payload.get('request_id') == request_id
                       for event in events._items)

    def test_timeout_releases_process_and_allows_reconnect(self):
        adapter, blocked, entered = self.make_isolated("connect")
        adapter.initialize()
        original = adapter._process
        adapter.operation_timeout_s = 0.2
        with self.assertRaisesRegex(TimeoutError, "超时"):
            adapter.connect()
        self.assertTrue(entered.is_set())
        self.assertIsNone(adapter._process)
        self.assertTrue(original._closed)
        blocked.clear()
        adapter.operation_timeout_s = 3.0
        self.assertEqual(adapter.connect().info.serial, "SIMULATION")

    def test_blocked_spectrum_read_times_out_and_reaps_process(self):
        adapter, blocked, entered = self.make_isolated("acquire")
        adapter.connect()
        context = adapter.apply_configuration(AcquisitionRequest(10000, 1, 0, 80))
        adapter.operation_timeout_s = 0.2
        with self.assertRaisesRegex(TimeoutError, "读取光谱超时"):
            adapter.acquire(context)
        self.assertTrue(entered.is_set())
        self.assertIsNone(adapter._process)

    def test_acquisition_budget_allows_long_exposure_and_averaging(self):
        adapter, blocked, entered = self.make_isolated("acquire")
        adapter.connect()
        context = adapter.apply_configuration(AcquisitionRequest(1_000_000, 2, 0, 80))
        adapter.operation_timeout_s = 0.1
        timer = threading.Timer(0.4, blocked.clear)
        timer.start()
        try:
            frame = adapter.acquire(context)
            self.assertEqual(len(frame.values), context.pixel_count)
        finally:
            timer.cancel()
            timer.join()

    def test_cancel_in_gap_before_read_cannot_be_lost(self):
        adapter, blocked, entered = self.make_isolated("acquire")
        adapter.connect()
        context = adapter.apply_configuration(AcquisitionRequest(10000, 1, 0, 80))
        adapter.cancel_pending()
        with self.assertRaises(SpectrometerInterrupted):
            adapter.acquire(context)
        self.assertFalse(entered.is_set())
        self.assertIsNone(adapter._process)
        blocked.clear()
        self.assertIsNotNone(adapter.connect())

    def test_device_process_crash_reports_error_and_can_reconnect(self):
        adapter, blocked, entered = self.make_isolated("crash")
        adapter.connect()
        context = adapter.apply_configuration(AcquisitionRequest(10000, 1, 0, 80))
        with self.assertRaisesRegex(RuntimeError, "设备进程"):
            adapter.acquire(context)
        self.assertIsNone(adapter._process)
        self.assertIsNotNone(adapter.connect())

    def test_cancel_blocked_single_discards_frame_and_reuses_connection(self):
        adapter, blocked, entered = self.make_isolated("acquire")
        events = CoalescingEventQueue()
        service = AcquisitionService(lambda: adapter, events)
        service.start()
        try:
            service.send("connect")
            self.wait_until(lambda: service.connected is not None)
            service.send("configure_single", request_id="single-1", integration_us=10000, averages=1)
            self.assertTrue(entered.wait(3.0))
            self.assertEqual(service.state, AcquisitionState.ACQUIRING)
            process = adapter._process
            service.send("stop")
            self.assertFalse(adapter._cancelled.is_set())
            self.assertIsNotNone(service.connected)
            blocked.clear()
            self.wait_until(lambda: self.pause_acknowledged(events, 'single-1'))
            delivered = []
            while not events.empty():
                delivered.append(events.get_nowait())
            self.assertFalse(any(event.kind == "spectrum" for event in delivered))
            self.assertFalse(any(event.kind in ("error", "disconnected") for event in delivered))
            self.assertTrue(any(event.kind == "acquisition_state" and event.payload.get("cancelled") for event in delivered))
            self.assertIs(adapter._process, process)
            service.send("configure_single", request_id="single-2", integration_us=10000, averages=1)
            self.wait_until(lambda: any(event.kind == "spectrum" for event in list(events._items)))
            frames = []
            while not events.empty():
                event = events.get_nowait()
                if event.kind == "spectrum":
                    frames.append(event)
            self.assertEqual([event.payload["request_id"] for event in frames], ["single-2"])
            self.assertIs(adapter._process, process)
        finally:
            service.send("shutdown")
            service.join(3.0)
            self.assertFalse(service.is_alive())

    def test_disconnect_during_blocked_acquisition_is_bounded(self):
        adapter, blocked, entered = self.make_isolated("acquire")
        service = AcquisitionService(lambda: adapter, CoalescingEventQueue())
        service.start()
        try:
            service.send("connect")
            self.wait_until(lambda: service.connected is not None)
            service.send("configure_single", request_id="single-1")
            self.assertTrue(entered.wait(3.0))
            service.send("disconnect")
            self.wait_until(lambda: service.connected is None)
        finally:
            service.send("shutdown")
            service.join(3.0)
            self.assertFalse(service.is_alive())

    def test_shutdown_during_blocked_read_reaps_child_process(self):
        adapter, blocked, entered = self.make_isolated("acquire")
        service = AcquisitionService(lambda: adapter, CoalescingEventQueue())
        service.start()
        service.send("connect")
        try:
            self.wait_until(lambda: service.connected is not None)
            service.send("configure_single", request_id="single-1")
            self.assertTrue(entered.wait(3.0))
        finally:
            service.send("shutdown")
            service.join(3.0)
        self.assertFalse(service.is_alive())
        self.assertIsNone(adapter._process)

    def test_cancel_before_read_and_single_events_never_coalesce(self):
        adapter = SimulatedSpectrometer()
        events = CoalescingEventQueue()
        service = AcquisitionService(lambda: adapter, events)
        service.spectrometer = adapter
        service.connected = adapter.connect()
        service.send("stop")
        service._configure("single", {"request_id": "cancelled"})
        self.assertIsNone(service.context)
        self.assertIsNotNone(service.connected)
        self.assertTrue(events.empty())
        events.put(("spectrum", {"continuous": False, "request_id": "a"}))
        events.put(("spectrum", {"continuous": False, "request_id": "b"}))
        self.assertEqual(events.qsize(), 2)

    def test_continuous_pause_repeatedly_reuses_same_device_process(self):
        adapter, blocked, entered = self.make_isolated("acquire")
        events = CoalescingEventQueue()
        service = AcquisitionService(lambda: adapter, events)
        service.start()
        try:
            service.send("connect")
            self.wait_until(lambda: service.connected is not None)
            process = adapter._process
            for index in range(3):
                entered.clear()
                blocked.set()
                request_id = 'stream-' + str(index)
                service.send("configure_start", request_id=request_id, integration_us=10000, averages=1)
                self.assertTrue(entered.wait(3.0))
                service.send("stop")
                self.assertFalse(adapter._cancelled.is_set())
                self.assertIs(adapter._process, process)
                blocked.clear()
                self.wait_until(lambda: self.pause_acknowledged(events, request_id))
                delivered = []
                while not events.empty():
                    delivered.append(events.get_nowait())
                self.assertFalse(any(event.kind in ('spectrum', 'error', 'disconnected') for event in delivered))
                self.assertIsNotNone(service.context)
                self.assertIs(adapter._process, process)
            service.send('configure_single', request_id='after-pauses')
            self.wait_until(lambda: events.qsize() > 0 and service.state == AcquisitionState.IDLE)
            frames = []
            while not events.empty():
                event = events.get_nowait()
                if event.kind == 'spectrum':
                    frames.append(event.payload['request_id'])
            self.assertEqual(frames, ['after-pauses'])
            self.assertIs(adapter._process, process)
        finally:
            blocked.clear()
            service.send('shutdown')
            service.join(3.0)
            self.assertFalse(service.is_alive())

    def test_pause_during_configuration_keeps_verified_context_and_skips_read(self):
        for action in ('configure_start', 'configure_single'):
            with self.subTest(action=action):
                adapter, blocked, entered = self.make_isolated('apply_configuration')
                events = CoalescingEventQueue()
                service = AcquisitionService(lambda: adapter, events)
                service.start()
                try:
                    service.send('connect')
                    self.wait_until(lambda: service.connected is not None)
                    process = adapter._process
                    service.send(action, request_id='cancel-config', integration_us=20000, averages=2)
                    self.assertTrue(entered.wait(3.0))
                    service.send('stop')
                    blocked.clear()
                    self.wait_until(lambda: self.pause_acknowledged(events, 'cancel-config'))
                    delivered = []
                    while not events.empty():
                        delivered.append(events.get_nowait())
                    self.assertFalse(any(event.kind in ('spectrum', 'error', 'disconnected') for event in delivered))
                    self.assertFalse(any(event.kind == 'acquisition_state' and event.payload['running'] for event in delivered))
                    self.assertEqual(service.context.integration_us, 20000)
                    self.assertEqual(service.context.averages, 2)
                    self.assertIs(adapter._process, process)
                finally:
                    blocked.clear()
                    service.send('shutdown')
                    service.join(3.0)
                    self.assertFalse(service.is_alive())

    def test_pause_in_gap_before_read_skips_sdk_call_and_retains_context(self):
        class CountingSpectrometer(SimulatedSpectrometer):
            reads = 0

            def acquire(self, context):
                self.reads += 1
                return super().acquire(context)

        adapter = CountingSpectrometer()
        service = AcquisitionService(lambda: adapter, CoalescingEventQueue())
        service.spectrometer = adapter
        service.connected = adapter.connect()
        service.context = adapter.apply_configuration(AcquisitionRequest(10000, 1, 0, 80))
        context = service.context
        service.send('stop')
        service._acquire_frame()
        self.assertEqual(adapter.reads, 0)
        service._handle(service.commands.get_nowait())
        self.assertIs(service.context, context)
        self.assertIsNotNone(service.connected)
        service._single()
        self.assertEqual(adapter.reads, 1)

    def test_paused_read_that_never_returns_still_times_out_and_can_reconnect(self):
        adapter, blocked, entered = self.make_isolated('acquire')
        events = CoalescingEventQueue()
        service = AcquisitionService(lambda: adapter, events)
        service.start()
        try:
            service.send('connect')
            self.wait_until(lambda: service.connected is not None)
            service.send('configure_single', request_id='timeout', integration_us=10000, averages=1)
            self.assertTrue(entered.wait(3.0))
            service.send('stop')
            self.wait_until(lambda: service.connected is None and service.commands.empty(), timeout=7.0)
            self.assertIsNone(adapter._process)
            delivered = []
            while not events.empty():
                delivered.append(events.get_nowait())
            self.assertFalse(any(event.kind == 'spectrum' for event in delivered))
            self.assertTrue(any(event.kind == 'error' and '超时' in event.payload['message'] for event in delivered))
            blocked.clear()
            service.send('connect')
            self.wait_until(lambda: service.connected is not None)
            self.assertIsNotNone(adapter._process)
        finally:
            blocked.clear()
            service.send('shutdown')
            service.join(3.0)
            self.assertFalse(service.is_alive())

    def test_failed_configuration_releases_previous_context(self):
        class RejectingSpectrometer(SimulatedSpectrometer):
            def apply_configuration(self, request):
                raise RuntimeError("readback rejected")

        adapter = RejectingSpectrometer()
        events = CoalescingEventQueue()
        service = AcquisitionService(lambda: adapter, events)
        service.start()
        try:
            service.send("connect")
            self.wait_until(lambda: service.connected is not None)
            service.send("configure_single", request_id="rejected")
            self.wait_until(lambda: service.connected is None)
            self.assertIsNone(service.context)
        finally:
            service.send("shutdown")
            service.join(3.0)
        self.assertFalse(service.is_alive())

    def test_sdk_initialization_failure_thread_terminates(self):
        def failing_factory():
            raise RuntimeError("SDK unavailable")

        service = AcquisitionService(failing_factory, CoalescingEventQueue())
        service.start()
        service.join(2.0)
        self.assertFalse(service.is_alive())
        self.assertNotIn(service, threading.enumerate())
