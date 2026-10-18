"""Bounded FileFlows sampling; stdout is a single decision, media is read-only."""
import json
import math
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DEADLINE = float('inf')


def run(args, timeout=90):
    remaining = DEADLINE - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired(args[0], 600)
    result = subprocess.run(args, capture_output=True, text=True, timeout=min(timeout, remaining))
    if result.returncode:
        raise ValueError(result.stderr[-1200:])
    return result.stdout, result.stderr


def probe(tool, path, extra=()):
    out, _ = run([tool, '-v', 'error', *extra, '-of', 'json', str(path)])
    return json.loads(out)


def windows(duration):
    length = min(20.0, duration / 12)
    return [(max(0, min(duration - length, duration * point - length / 2)), length)
            for point in (0.15, 0.5, 0.85)]


def prediction(sizes, lengths, duration, overhead):
    return sum(sizes) / sum(lengths) * duration + overhead


def worthwhile(expected, original):
    return math.isfinite(expected) and 0 < expected <= original * 0.90


def compatible_quality(candidate, baseline):
    # Compare every scene: a good average must not hide a worse individual scene.
    return len(candidate) == len(baseline) == 3 and all(
        math.isfinite(a) and a >= b for a, b in zip(candidate, baseline))


def decide(settings):
    global DEADLINE
    DEADLINE = time.monotonic() + 600
    ffmpeg, ffprobe, source = (settings[k] for k in ('ffmpeg', 'ffprobe', 'source'))
    info = probe(ffprobe, source, ['-show_streams', '-show_format'])
    videos = [s for s in info['streams'] if s['codec_type'] == 'video']
    if len(videos) != 1 or videos[0].get('disposition', {}).get('attached_pic'):
        return {'encode': False, 'reason': 'Ambiguous video stream selection; preserve original'}
    video = videos[0]
    if video.get('closed_captions'):
        return {'encode': True, 'mode': 'CQP', 'reason': 'Preserve A53 captions with the existing QP22 encode, even if slow'}
    duration = float(info['format']['duration'])
    original = int(info['format']['size'])
    if not math.isfinite(duration) or duration <= 0 or original <= 0:
        raise ValueError('Missing duration or size')
    samples = windows(duration)
    # Captions may first appear later than ffprobe's initial stream inspection.
    packet_rates = []
    for start, length in samples:
        frames = probe(ffprobe, source, ['-select_streams', 'v:0', '-read_intervals',
                       f'{start}%+{length}', '-show_frames',
                       '-show_entries', 'frame=side_data_list'])
        if any('A53' in side.get('side_data_type', '') for frame in frames.get('frames', [])
               for side in frame.get('side_data_list', [])):
            return {'encode': True, 'mode': 'CQP', 'reason': 'Sample contains A53 captions; use existing QP22 encode without a speed limit'}
        packets = probe(ffprobe, source, ['-select_streams', 'v:0', '-read_intervals',
                        f'{start}%+{length}', '-show_packets',
                        '-show_entries', 'packet=size,duration_time']).get('packets', [])
        seconds = sum(float(p.get('duration_time', 0)) for p in packets)
        if seconds <= 0:
            raise ValueError('Cannot estimate video bitrate from packets')
        packet_rates.append(sum(int(p['size']) for p in packets) * 8 / seconds)
    hdr = settings.get('hdr') or settings.get('dolby_vision') or video.get('color_transfer') in ('smpte2084', 'arib-std-b67')
    high_depth = video.get('pix_fmt') in ('yuv420p10le', 'p010le')
    if video.get('pix_fmt') not in ('yuv420p', 'nv12', 'yuv420p10le', 'p010le'):
        return {'encode': True, 'mode': 'CQP', 'reason': 'Unsupported sampling pixel format; preserve existing encoder settings'}
    filters = settings.get('filters', [])
    if any(not re.fullmatch(r'crop=\d+:\d+:\d+:\d+', f) for f in filters):
        return {'encode': True, 'mode': 'CQP', 'reason': 'Unrecognized sampling filter; preserve existing encoder settings'}
    tags = {k.upper(): v for k, v in video.get('tags', {}).items()}
    stream_bytes = tags.get('NUMBER_OF_BYTES')
    rate = float(video.get('bit_rate') or tags.get('BPS') or sum(packet_rates) / 3)
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError('Invalid source video bitrate')
    # Reserve every original nonvideo stream, even tracks the flow later removes.
    # Without stream byte metadata, overestimate overhead from the lowest sample.
    video_bytes = int(stream_bytes) if stream_bytes else min(packet_rates) * duration / 8
    overhead = max(0, original - video_bytes) + original * 0.02
    crop = ','.join(filters)
    transform = (crop + ',' if crop else '') + ('format=p010le,hwupload' if high_depth else 'format=nv12,hwupload')
    color = []
    for key, option in [('color_primaries', '-color_primaries'), ('color_transfer', '-color_trc'), ('color_space', '-colorspace')]:
        if video.get(key) not in (None, 'unknown', 'unspecified'):
            color.extend([option, str(video[key])])
    base = [ffmpeg, '-hide_banner', '-loglevel', 'warning', '-y', '-filter_threads', '1',
            '-vaapi_device', '/dev/dri/renderD128', '-threads', '1']
    attempts = []
    with tempfile.TemporaryDirectory(prefix='size-preflight-', dir=settings['temp']) as temporary:
        temp = Path(temporary)

        def encode(mode, options):
            paths, lengths = [], []
            for i, (start, length) in enumerate(samples):
                path = temp / f'{mode}-{i}.mkv'
                run([*base, '-ss', str(start), '-i', source, '-t', str(length),
                     '-map', '0:v:0', '-an', '-sn', '-dn', '-vf', transform,
                     '-c:v', 'hevc_vaapi', *options, *color, str(path)])
                measured = probe(ffprobe, path, ['-show_streams', '-show_format'])
                if abs(float(measured['format']['duration']) - length) > 1:
                    raise ValueError('Sample duration mismatch')
                paths.append(path)
                lengths.append(float(measured['format']['duration']))
            return paths, lengths

        def quality(paths):
            scores = []
            reference = (crop + ',' if crop else '') + 'setpts=PTS-STARTPTS'
            for path, (start, length) in zip(paths, samples):
                _, stderr = run([ffmpeg, '-hide_banner', '-loglevel', 'info',
                                 '-filter_complex_threads', '1', '-threads', '1',
                                 '-ss', str(start), '-i', source, '-i', str(path),
                                 '-filter_complex', f'[0:v:0]{reference}[ref];[1:v:0]setpts=PTS-STARTPTS[enc];[ref][enc]ssim=shortest=1',
                                 '-t', str(length), '-an', '-sn', '-f', 'null', '-'])
                match = re.search(r'All:([0-9.]+)', stderr)
                if not match:
                    raise ValueError('No sample quality score')
                scores.append(float(match[1]))
            return scores

        baseline, lengths = encode('CQP', ['-qp', '22'])
        expected = prediction([p.stat().st_size for p in baseline], lengths, duration, overhead)
        attempts.append({'mode': 'CQP', 'expected_bytes': round(expected),
                         'source_video_bps': round(rate),
                         'source_rate_kind': 'metadata' if video.get('bit_rate') or tags.get('BPS') else 'sampled packets',
                         'nonvideo_reserve_bytes': round(overhead)})
        if worthwhile(expected, original):
            return {'encode': True, 'mode': 'CQP', 'reason': 'QP22 samples predict at least 10% savings', 'attempts': attempts}
        if hdr or high_depth:
            return {'encode': False, 'reason': 'HDR/10-bit QP22 samples do not predict worthwhile savings; preserve original', 'attempts': attempts}
        baseline_quality = quality(baseline)
        for ratio in (0.9, 0.75):
            bitrate = int(rate * ratio)
            paths, lengths = encode(f'VBR{ratio}', ['-rc_mode', 'VBR', '-b:v', str(bitrate),
                           '-maxrate', str(int(rate)), '-bufsize', str(int(rate * 2))])
            expected = prediction([p.stat().st_size for p in paths], lengths, duration, overhead)
            scores = quality(paths)
            attempts.append({'mode': 'VBR', 'ratio': ratio, 'bitrate': bitrate,
                             'expected_bytes': round(expected), 'ssim': scores,
                             'baseline_ssim': baseline_quality})
            if worthwhile(expected, original) and compatible_quality(scores, baseline_quality):
                return {'encode': True, 'mode': 'VBR', 'bitrate': bitrate, 'maxrate': int(rate),
                        'reason': 'Source-relative VBR saves space without lower sampled SSIM than QP22', 'attempts': attempts}
        return {'encode': False, 'reason': 'No sampled candidate meets size and quality checks', 'attempts': attempts}


def evaluate(settings):
    try:
        return decide(settings)
    except subprocess.TimeoutExpired:
        return {'encode': True, 'mode': 'CQP', 'reason': 'Sampling timed out; continue existing QP22 encode without a speed limit'}
    except (ValueError, KeyError, StopIteration, OSError) as error:
        return {'encode': False, 'reason': f'Preflight failed; preserve original: {error}'}


if __name__ == '__main__':
    print(json.dumps(evaluate(json.loads(sys.argv[1])), allow_nan=False))
