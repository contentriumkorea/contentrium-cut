const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const sha = require('../plugin/vendor/sha256');
const guard = require('../plugin/guard');
const mutation = require('../plugin/mutation');
const TPF = 8467200000n;
const tick = frame => BigInt(frame) * TPF;
const tm = value => ({ticks: String(value)});

// Only Adobe's unavailable SDK boundary is simulated. The real host snapshot,
// clone/trim/cleanup/readback and durable mutation controller run unchanged.
function fixture({overlay = false, range = [0, 120], misroute = false, cleanupNoOp = false} = {}) {
  const sequences = [];
  const media = new Map();
  for (const id of ['A', 'B', 'audio', 'logo']) media.set(id, {
    getId: () => id,
    getMediaFilePath: async () => 'D:/fixture/' + id + '.mov',
    isOffline: async () => false,
    isSequence: async () => false,
    isMergedClip: async () => false,
    isMulticamClip: async () => false,
    hasProxy: async () => false,
  });
  function makeItem(sequence, track, data) {
    return {
      sequence, track, data,
      getProjectItem: async () => media.get(data.asset),
      getStartTime: async () => tm(data.start), getEndTime: async () => tm(data.end),
      getInPoint: async () => tm(data.inside), getOutPoint: async () => tm(data.out),
      getSpeed: async () => 1, isSpeedReversed: async () => false,
      isDisabled: async () => false, isAdjustmentLayer: async () => false,
      getComponentChain: async () => ({getComponentCount: () => 1, getComponentAtIndex: () => ({
        getMatchName: async () => 'fixture.opacity', getParamCount: () => 1,
        getParam: () => ({getKeyframeListAsTickTimes: () => [], getValueAtTime: async () => data.opacity}),
      })}),
      createSetInPointAction: next => () => {
        const value = BigInt(next.ticks); data.start += value - data.inside; data.inside = value;
      },
      createSetOutPointAction: next => () => {
        data.out = BigInt(next.ticks); data.end = data.start + data.out - data.inside;
      },
    };
  }
  function makeTrack(sequence, kind, index, rows = []) {
    const track = {name: kind + ' ' + index, index, kind, items: [], isMuted: async () => false,
      createSetNameAction: name => () => {track.name = name;},
      getTrackItems: type => type === 'clip' ? track.items.slice().sort((a, b) => Number(a.data.start - b.data.start)) : []};
    track.items = rows.map(row => makeItem(sequence, track, structuredClone(row)));
    return track;
  }
  function makeSequence(id, videos, audios) {
    const sequence = {guid: id, name: 'Interview', videos: [], audios: [],
      getTimebase: async () => String(TPF),
      getVideoTrackCount: async () => sequence.videos.length,
      getAudioTrackCount: async () => sequence.audios.length,
      getVideoTrack: async index => sequence.videos[index],
      getAudioTrack: async index => sequence.audios[index],
      getEndTime: async () => tm(sequence.videos.concat(sequence.audios).flatMap(t => t.items).reduce((end, item) => item.data.end > end ? item.data.end : end, 0n)),
      getProjectItem: async () => ({createSetNameAction: name => () => {sequence.name = name;}}),
      createCloneAction: () => () => sequences.push(makeSequence('result', sequence.videos.map(t => t.items.map(i => i.data)), sequence.audios.map(t => t.items.map(i => i.data)))),
    };
    sequence.videos = videos.map((rows, i) => makeTrack(sequence, 'video', i, rows));
    sequence.audios = audios.map((rows, i) => makeTrack(sequence, 'audio', i, rows));
    return sequence;
  }
  const row = (asset, opacity = 100) => ({asset, start: 0n, end: tick(120), inside: tick(30), out: tick(150), opacity});
  const videos = [[row('A', 95)], [row('B', 90)]];
  if (overlay) videos.push([row('logo', 70)]);
  const original = makeSequence('source', videos, [[row('audio')]]);
  sequences.push(original);
  const project = {guid: 'project', name: 'Test', getSequences: async () => sequences,
    getActiveSequence: async () => original, openSequence: async () => true, save: async () => true,
    lockedAccess: callback => callback(),
    executeTransaction: build => {const actions = []; build({addAction: action => actions.push(action)}); actions.forEach(action => action()); return true;},
  };
  const ppro = {
    Project: {getActiveProject: async () => project, getProject: async () => project},
    Guid: {fromString: value => value}, ClipProjectItem: {cast: value => value},
    Constants: {TrackItemType: {CLIP: 'clip', TRANSITION: 'transition'}, MediaType: {VIDEO: 'video'}},
    TickTime: {createWithTicks: tm, TIME_ZERO: tm(0)},
    TrackItemSelection: {createEmptySelection: callback => {
      const items = []; callback({items, addItem: item => {items.push(item); return true;}});
    }},
    SequenceEditor: {getEditor: sequence => ({
      createCloneTrackItemAction: (source, offset, vertical, audioVertical, align, insert) => () => {
        assert.equal(audioVertical, 0); assert.equal(align, true); assert.equal(insert, false);
        let index = source.track.index + vertical;
        if (misroute && source.data.start > tick(120)) index = 0;
        while (sequence.videos.length <= index) sequence.videos.push(makeTrack(sequence, 'video', sequence.videos.length));
        const data = structuredClone(source.data); data.start += BigInt(offset.ticks); data.end += BigInt(offset.ticks);
        sequence.videos[index].items.push(makeItem(sequence, sequence.videos[index], data));
      },
      createRemoveItemsAction: (selection, ripple, kind, shift) => () => {
        assert.equal(ripple, false); assert.equal(kind, 'video'); assert.equal(shift, false);
        if (cleanupNoOp && selection.items.every(item => item.data.start > tick(120))) return;
        for (const item of selection.items) item.track.items.splice(item.track.items.indexOf(item), 1);
      },
    })},
  };
  const context = {sha256: sha, require: name => {
    if (name === 'premierepro') return ppro;
    if (name === 'uxp') return {host: {version: '26.5.2'}};
    if (name === './guard.js') return guard;
    if (name === './mutation.js') return mutation;
    throw new Error('Unexpected dependency ' + name);
  }};
  vm.runInNewContext(fs.readFileSync(require.resolve('../plugin/host.js'), 'utf8'), context);
  const host = context.ContentriumHost;
  async function apply() {
    const before = (await host.snapshot()).snapshot;
    const input = {...before, range: {startFrame: range[0], endFrame: range[1]}, hostSnapshotHash: before.snapshotHash};
    delete input.snapshotHash; input.snapshotHash = host.hash(input);
    const segments = [[range[0], 60, 'A'], [60, range[1], 'B']].map(([startFrame, endFrame, asset]) => ({
      startFrame, endFrame, cameraId: asset,
      sourceClipInstanceKey: before.clips.find(c => c.assetId === asset).instanceKey,
      sourceIn: String(tick(startFrame + 30)), sourceOut: String(tick(endFrame + 30)), reason: 'speaker',
    }));
    const plan = {snapshotHash: input.snapshotHash, segments}; plan.planHash = host.hash(plan);
    const receipt = await host.apply(plan, input, ['video:0', 'video:1'], {
      check: async () => {}, beforeBatch: async () => ({execute: true}), afterBatch: async () => {}, onResult: async () => {},
    });
    return {receipt, before};
  }
  return {apply, sequences, original};
}

test('editable source clips fill one named top video track without an extra staging track', async () => {
  const f = fixture(); const {receipt, before} = await f.apply();
  assert.equal(f.sequences[1].videos.length, 3, 'only the final output track may be added');
  assert.deepEqual(Array.from(receipt.segments, c => [c.trackRef, c.assetId, c.startTicks, c.endTicks]), [
    ['video:2', 'A', '0', '508032000000'], ['video:2', 'B', '508032000000', '1016064000000'],
  ]);
  assert.equal(f.sequences[1].videos[0].items.length, 0);
  assert.deepEqual(f.original.videos.map(t => t.items.map(i => i.data.asset)), [['A'], ['B']]);
  assert.equal(f.sequences[1].videos[1].items.length, 0);
  assert.equal(f.sequences[1].videos[2].name, 'Contentrium CUT');
  assert.equal(f.sequences[1].videos[2].items.length, 2);
  assert.equal(f.sequences[1].videos[2].items.every(i => typeof i.createSetInPointAction === 'function'), true);
  assert.equal(receipt.outputTrackRef, 'video:2');
  assert.deepEqual(Array.from(receipt.snapshot.clips.filter(c => c.mediaType === 'audio'), c => c.effectFingerprint), Array.from(before.clips.filter(c => c.mediaType === 'audio'), c => c.effectFingerprint));
});

test('existing graphics are untouched while cuts occupy the absolute highest video track', async () => {
  const f = fixture({overlay: true}); const {receipt, before} = await f.apply();
  assert.equal(f.sequences[1].videos.length, 4);
  const saved = receipt.snapshot.clips.find(c => c.assetId === 'logo');
  const source = before.clips.find(c => c.assetId === 'logo');
  for (const field of ['trackRef', 'startTicks', 'endTicks', 'inTicks', 'outTicks', 'effectFingerprint']) assert.equal(saved[field], source[field]);
  assert.equal(receipt.segments.every(c => c.trackRef === 'video:3'), true);
  assert.equal(f.sequences[1].videos[3].name, 'Contentrium CUT');
});

test('selected-range cuts preserve both outside fragments on their original camera tracks', async () => {
  const f = fixture({range: [30, 90]}); const {receipt} = await f.apply();
  const rows = receipt.snapshot.clips.filter(c => c.mediaType === 'video');
  for (const [track, asset] of [['video:0', 'A'], ['video:1', 'B']]) {
    assert.equal(rows.filter(c => c.trackRef === track && c.assetId === asset && c.startTicks === '0' && c.endTicks === '254016000000').length, 1);
    assert.equal(rows.filter(c => c.trackRef === track && c.assetId === asset && c.startTicks === '762048000000' && c.endTicks === '1016064000000').length, 1);
  }
  assert.equal(receipt.segments.length, 2);
  assert.equal(f.sequences[1].videos.length, 3);
  assert.equal(receipt.segments.every(c => c.trackRef === 'video:2'), true);
});

test('wrong-track SDK readback cannot be reported as a successful edit', async () => {
  const f = fixture({misroute: true});
  await assert.rejects(f.apply(), /HOST_OUTPUT_TRACK_MISMATCH/);
  assert.deepEqual(f.original.videos.map(t => t.items.map(i => i.data.asset)), [['A'], ['B']]);
});

test('a successful SDK cleanup return with residual staged clips cannot report completion', async () => {
  const f = fixture({cleanupNoOp: true});
  await assert.rejects(f.apply(), /HOST_STAGE_CLEANUP_MISMATCH/);
  assert.deepEqual(f.original.videos.map(t => t.items.map(i => i.data.asset)), [['A'], ['B']]);
});
