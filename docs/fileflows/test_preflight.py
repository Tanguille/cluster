import unittest
from unittest.mock import patch

import preflight


class SafetyTests(unittest.TestCase):
    def test_samples_are_bounded_and_separate_for_short_and_long_files(self):
        for duration in (1, 15, 120, 7200):
            samples = preflight.windows(duration)
            for start, length in samples:
                self.assertGreater(length, 0)
                self.assertGreaterEqual(start, 0)
                self.assertLessEqual(start + length, duration)
            self.assertTrue(all(a + length < b for (a, length), (b, _) in zip(samples, samples[1:])))

    def test_audio_overhead_can_reject_video_that_alone_looks_smaller(self):
        predicted = preflight.prediction([50, 50, 50], [10, 10, 10], 100, 450)
        self.assertEqual(predicted, 950)
        self.assertFalse(preflight.worthwhile(predicted, 1000))

    def test_one_bad_scene_rejects_good_average(self):
        self.assertFalse(preflight.compatible_quality([1, 1, .98], [.99, .99, .99]))
        self.assertFalse(preflight.compatible_quality([1, 1, float('nan')], [.99] * 3))
        self.assertTrue(preflight.compatible_quality([.991] * 3, [.99] * 3))

    def test_caption_detection_keeps_original_encoder_and_skips_sampling(self):
        with patch.object(preflight, 'probe', return_value={'streams': [{'codec_type': 'video', 'closed_captions': 1}]}), patch.object(preflight, 'run') as runner:
            result = preflight.decide({'ffmpeg': 'ffmpeg', 'ffprobe': 'ffprobe', 'source': '/a file.mkv'})
        self.assertTrue(result['encode'])
        self.assertEqual(result['mode'], 'CQP')
        runner.assert_not_called()

    def test_slow_sampling_falls_back_to_existing_encoder(self):
        with patch.object(preflight, 'decide', side_effect=preflight.subprocess.TimeoutExpired('ffmpeg', 90)):
            result = preflight.evaluate({})
        self.assertTrue(result['encode'])
        self.assertEqual(result['mode'], 'CQP')

    def test_real_probe_failure_still_preserves_original(self):
        with patch.object(preflight, 'decide', side_effect=ValueError('invalid media')):
            self.assertFalse(preflight.evaluate({})['encode'])

    def test_command_failure_does_not_look_like_a_valid_sample(self):
        with patch.object(preflight.subprocess, 'run', return_value=preflight.subprocess.CompletedProcess([], 1, '', 'VAAPI failed')):
            with self.assertRaisesRegex(ValueError, 'VAAPI failed'):
                preflight.run(['ffmpeg'])


if __name__ == '__main__':
    unittest.main()
