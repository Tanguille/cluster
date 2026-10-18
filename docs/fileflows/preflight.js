/**
 * @name Video - Size and rate-control preflight
 * @description Sample before full encoding; preserve the existing encoder on captions or slow sampling.
 * @output Encode
 * @output Keep original
 * @output Adaptive bitrate encode
 */
function Script() {
    try {
        const model = Variables.FfmpegBuilderModel;
        if (!model || model.VideoStreams.length !== 1) return 2;
        const video = model.VideoStreams[0];
        if (video.Deleted || video.Stream.IsImage || video.Stream.TypeIndex !== 0) return 2;
        const encoding = Array.from(video.EncodingParameters || []).join(' ').trim();
        Logger.ILog('Preflight native encoding parameters: ' + encoding);
        if (!/^hevc_vaapi -qp 22(?: -preset veryslow)?$/.test(encoding)) {
            Logger.WLog('Unsupported sampling profile; using existing native encoder settings');
            return 1;
        }
        for (const extra of [video.OptionalFilter, video.OptionalEncodingParameters,
                             video.AdditionalParameters, model.CustomParameters]) {
            if (extra && extra.length) {
                Logger.WLog('Additional native parameters require sampling validation; using existing encoder settings');
                return 1;
            }
        }
        const result = Flow.Execute({
            command: 'python3',
            argumentList: ['/app/Data/fileflows-preflight.py', JSON.stringify({
                source: Variables.file.Orig.FullName,
                temp: Flow.TempPath,
                ffmpeg: Flow.GetToolPath('ffmpeg'),
                ffprobe: Flow.GetToolPath('ffprobe'),
                hdr: !!video.Stream.HDR,
                dolby_vision: !!video.Stream.DolbyVision,
                filters: Array.from(video.Filters || [])
            })],
            timeout: 660
        });
        if (!result.completed || result.exitCode !== 0) {
            Logger.WLog('Size preflight unavailable; continuing with existing encoder settings');
            return 1;
        }
        const decision = JSON.parse(result.standardOutput);
        Logger.ILog('Size preflight: ' + JSON.stringify(decision));
        if (!decision.encode) return 2;
        if (decision.mode === 'VBR') {
            if (!(Number.isFinite(decision.bitrate) && Number.isFinite(decision.maxrate) &&
                  decision.bitrate > 0 && decision.maxrate >= decision.bitrate)) return 2;
            Variables.AdaptiveVideoEncoding = 'hevc_vaapi -rc_mode VBR -b:v ' + Math.floor(decision.bitrate) +
                ' -maxrate ' + Math.floor(decision.maxrate) + ' -bufsize ' + Math.floor(decision.maxrate * 2);
            return 3;
        }
        return 1;
    } catch (error) {
        Logger.WLog('Preflight unavailable; continuing with existing encoder settings: ' + error);
        return 1;
    }
}
