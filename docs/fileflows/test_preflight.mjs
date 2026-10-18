import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const code = fs.readFileSync(new URL('./preflight.js', import.meta.url), 'utf8');
function invoke(decision, changes = {}) {
    const video = {Stream: {TypeIndex: 0, HDR: false, DolbyVision: false},
        EncodingParameters: ['hevc_vaapi', '-qp', '22', '-preset', 'veryslow'], Filters: []};
    const context = {Variables: {FfmpegBuilderModel: {VideoStreams: [video]},
        file: {Orig: {FullName: '/a file with "quotes".mkv'}}},
        Logger: {ILog() {}, WLog() {}},
        Flow: {TempPath: '/temp/test', GetToolPath: tool => '/tools/' + tool,
            Execute(args) {
                assert.equal(args.argumentList.length, 2);
                assert.equal(JSON.parse(args.argumentList[1]).source, '/a file with "quotes".mkv');
                return {completed: true, exitCode: 0, standardOutput: JSON.stringify(decision)};
            }}};
    Object.assign(context, changes);
    vm.createContext(context);
    vm.runInContext(code, context);
    const result = vm.runInContext('Script()', context);
    return {result, context};
}
assert.equal(invoke({encode: false}).result, 2);
assert.equal(invoke({encode: true, mode: 'CQP'}).result, 1);
const adaptive = invoke({encode: true, mode: 'VBR', bitrate: 1000000, maxrate: 2000000});
assert.equal(adaptive.result, 3);
assert.equal(adaptive.context.Variables.AdaptiveVideoEncoding,
    'hevc_vaapi -rc_mode VBR -b:v 1000000 -maxrate 2000000 -bufsize 4000000');
assert.equal(invoke({encode: true, mode: 'VBR', bitrate: -1, maxrate: 2}).result, 2);
assert.equal(invoke({}, {Flow: {Execute() {throw new Error('timeout');}}}).result, 1);
assert.equal(invoke({}, {Variables: {}}).result, 2);
console.log('Wrapper routing, safe argument passing and error fallback: passed');
