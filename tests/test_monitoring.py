"""Unit tests for accelerator thermal monitoring.

Tests performance monitor behavior including timing recording, degradation
detection, cooldown triggering, and metrics calculation.
"""

import time
import unittest
from sentinelcam.monitoring import (
    AcceleratorPerformanceMonitor,
    CoralMonitor,
    DepthAIMonitor,
    GenericMonitor,
    create_monitor
)


class TestAcceleratorPerformanceMonitor(unittest.TestCase):
    """Test base monitor functionality."""

    def setUp(self):
        """Create monitor instance for testing."""
        self.monitor = AcceleratorPerformanceMonitor(
            accelerator_type='test',
            window_size=10,
            check_interval=5,
            degradation_threshold=1.5,
            cooldown_duration=1  # Short for testing
        )

    def test_initialization(self):
        """Test monitor initializes with correct parameters."""
        self.assertEqual(self.monitor.accelerator_type, 'test')
        self.assertEqual(self.monitor.window_size, 10)
        self.assertEqual(self.monitor.frame_count, 0)
        self.assertEqual(self.monitor.cooldown_count, 0)
        self.assertIsNone(self.monitor.baseline_time)

    def test_record_inference(self):
        """Test inference timing recording."""
        start = time.time()
        time.sleep(0.01)
        end = time.time()

        self.monitor.record_inference(start, end)

        self.assertEqual(self.monitor.frame_count, 1)
        self.assertEqual(len(self.monitor.inference_times), 1)
        self.assertGreater(self.monitor.inference_times[0], 0.009)

    def test_baseline_establishment(self):
        """Test baseline is established after window_size samples."""
        # Record window_size - 1 samples, baseline should not be set
        for _ in range(self.monitor.window_size - 1):
            self.monitor.record_inference(time.time(), time.time() + 0.01)

        self.assertIsNone(self.monitor.baseline_time)

        # Record one more to complete window
        self.monitor.record_inference(time.time(), time.time() + 0.01)

        self.assertIsNotNone(self.monitor.baseline_time)
        self.assertAlmostEqual(self.monitor.baseline_time, 0.01, places=3)

    def test_degradation_detection_before_baseline(self):
        """Test no degradation detected before baseline established."""
        self.monitor.record_inference(time.time(), time.time() + 0.05)
        self.assertFalse(self.monitor.is_degraded())

    def test_degradation_detection_normal_performance(self):
        """Test no degradation detected with normal performance."""
        # Establish baseline at 10ms
        for _ in range(self.monitor.window_size):
            self.monitor.record_inference(time.time(), time.time() + 0.01)

        # Add more samples at 10ms
        for _ in range(5):
            self.monitor.record_inference(time.time(), time.time() + 0.01)

        self.assertFalse(self.monitor.is_degraded())

    def test_degradation_detection_degraded_performance(self):
        """Test degradation detected when performance degrades."""
        # Establish baseline at 10ms
        for _ in range(self.monitor.window_size):
            self.monitor.record_inference(time.time(), time.time() + 0.01)

        # Add degraded samples at 20ms (2x baseline > 1.5x threshold)
        self.monitor.inference_times.clear()
        for _ in range(self.monitor.window_size):
            self.monitor.record_inference(time.time(), time.time() + 0.02)

        self.assertTrue(self.monitor.is_degraded())

    def test_should_check_now(self):
        """Test check interval logic."""
        for i in range(1, 20):
            self.monitor.record_inference(time.time(), time.time() + 0.01)

            if i % self.monitor.check_interval == 0:
                self.assertTrue(self.monitor.should_check_now())
            else:
                self.assertFalse(self.monitor.should_check_now())

    def test_trigger_cooldown(self):
        """Test cooldown triggering and state reset."""
        # Establish baseline and degrade
        for _ in range(self.monitor.window_size):
            self.monitor.record_inference(time.time(), time.time() + 0.01)

        initial_frame_count = self.monitor.frame_count

        # Trigger cooldown
        metrics = self.monitor.trigger_cooldown()

        # Verify cooldown recorded
        self.assertEqual(self.monitor.cooldown_count, 1)
        self.assertEqual(self.monitor.last_cooldown_frame, initial_frame_count)
        self.assertTrue(metrics['cooldown_triggered'])

        # Verify state reset
        self.assertEqual(len(self.monitor.inference_times), 0)
        self.assertIsNone(self.monitor.baseline_time)

    def test_get_metrics_empty(self):
        """Test metrics with no data."""
        metrics = self.monitor.get_metrics()

        self.assertEqual(metrics['accelerator'], 'test')
        self.assertEqual(metrics['frame_count'], 0)
        self.assertIsNone(metrics['baseline_ms'])
        self.assertIsNone(metrics['current_avg_ms'])
        self.assertFalse(metrics['degraded'])

    def test_get_metrics_with_data(self):
        """Test metrics calculation with data."""
        # Establish baseline
        for _ in range(self.monitor.window_size):
            self.monitor.record_inference(time.time(), time.time() + 0.01)

        metrics = self.monitor.get_metrics()

        self.assertEqual(metrics['frame_count'], self.monitor.window_size)
        self.assertAlmostEqual(metrics['baseline_ms'], 10.0, places=1)
        self.assertAlmostEqual(metrics['current_avg_ms'], 10.0, places=1)
        self.assertEqual(metrics['cooldown_count'], 0)


class TestDeviceSpecificMonitors(unittest.TestCase):
    """Test device-specific monitor subclasses."""

    def test_coral_monitor_creation(self):
        """Test CoralMonitor instantiation."""
        monitor = CoralMonitor(window_size=25)
        self.assertEqual(monitor.accelerator_type, 'coral')
        self.assertEqual(monitor.window_size, 25)

    def test_depthai_monitor_creation(self):
        """Test DepthAIMonitor instantiation."""
        monitor = DepthAIMonitor(window_size=30)
        self.assertEqual(monitor.accelerator_type, 'depthai')
        self.assertIsNone(monitor.device)

    def test_generic_monitor_creation(self):
        """Test GenericMonitor instantiation."""
        monitor = GenericMonitor(accelerator_type='ncs2')
        self.assertEqual(monitor.accelerator_type, 'ncs2')

    def test_depthai_temperature_without_device(self):
        """Test DepthAI temperature query without device."""
        monitor = DepthAIMonitor()
        temp = monitor._get_device_temperature()
        self.assertIsNone(temp)


class TestMonitorFactory(unittest.TestCase):
    """Test monitor factory function."""

    def test_create_coral_monitor(self):
        """Test factory creates CoralMonitor."""
        monitor = create_monitor('coral')
        self.assertIsInstance(monitor, CoralMonitor)
        self.assertEqual(monitor.accelerator_type, 'coral')

    def test_create_depthai_monitor(self):
        """Test factory creates DepthAIMonitor for various names."""
        for name in ['depthai', 'oak', 'oak1', 'oak-d']:
            monitor = create_monitor(name)
            self.assertIsInstance(monitor, DepthAIMonitor)

    def test_create_generic_monitor(self):
        """Test factory creates GenericMonitor for unknown types."""
        for name in ['ncs2', 'cpu', 'custom_accel']:
            monitor = create_monitor(name)
            self.assertIsInstance(monitor, GenericMonitor)

    def test_create_with_config_dict(self):
        """Test factory applies config dictionary."""
        config = {
            'window_size': 50,
            'check_interval': 30,
            'degradation_threshold': 1.3
        }
        monitor = create_monitor('coral', config=config)

        self.assertEqual(monitor.window_size, 50)
        self.assertEqual(monitor.check_interval, 30)
        self.assertEqual(monitor.degradation_threshold, 1.3)

    def test_create_with_kwargs_override(self):
        """Test factory kwargs override config dict."""
        config = {'window_size': 50}
        monitor = create_monitor('coral', config=config, window_size=100)

        self.assertEqual(monitor.window_size, 100)


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and error conditions."""

    def test_zero_inference_times(self):
        """Test handling of zero duration inference."""
        monitor = AcceleratorPerformanceMonitor('test', window_size=5)

        # Record zero-duration inference (shouldn't crash)
        t = time.time()
        monitor.record_inference(t, t)

        self.assertEqual(len(monitor.inference_times), 1)
        self.assertEqual(monitor.inference_times[0], 0.0)

    def test_degradation_with_partial_window(self):
        """Test degradation check with incomplete window."""
        monitor = AcceleratorPerformanceMonitor('test', window_size=10)

        # Record only 5 samples
        for _ in range(5):
            monitor.record_inference(time.time(), time.time() + 0.01)

        # Should not detect degradation (baseline not established)
        self.assertFalse(monitor.is_degraded())

    def test_multiple_cooldowns(self):
        """Test multiple cooldown cycles."""
        monitor = AcceleratorPerformanceMonitor('test',
                                               window_size=5,
                                               cooldown_duration=0.1)

        # Establish baseline
        for _ in range(5):
            monitor.record_inference(time.time(), time.time() + 0.01)

        # Trigger multiple cooldowns
        for i in range(3):
            monitor.trigger_cooldown()
            self.assertEqual(monitor.cooldown_count, i + 1)


if __name__ == '__main__':
    unittest.main()
