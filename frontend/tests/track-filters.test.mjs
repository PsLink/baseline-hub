import { test } from 'node:test'
import assert from 'node:assert/strict'
import { filterTracks } from '../src/features/hub/utils/trackFilters.ts'
const tracks = [
  { slug: 'a', name: 'A', model_name: 'Qwen', gpu_type: 'H100', status: 'open' },
  { slug: 'b', name: 'B', model_name: 'Llama', gpu_type: 'A100', status: 'draft' },
  { slug: 'c', name: 'C', model_name: 'Qwen', gpu_type: 'H100', status: 'retired' },
]
const defaults = { q: '', gpu: '', model: '', status: '', mine: false, hideRetired: true }
test('track facets combine and mine uses owned submission track slugs', () => {
  assert.deepEqual(filterTracks(tracks, defaults, new Set()), tracks.slice(0, 2))
  assert.deepEqual(filterTracks(tracks, { ...defaults, q: ' QWEN ', gpu: 'H100', model: 'Qwen', mine: true }, new Set(['a'])), [tracks[0]])
  assert.deepEqual(filterTracks(tracks, { ...defaults, mine: true }, new Set()), [])
  assert.deepEqual(filterTracks(tracks, { ...defaults, status: 'retired' }, new Set()), [tracks[2]])
  assert.deepEqual(filterTracks(tracks, { ...defaults, model: 'Qwen', gpu: 'A100' }, new Set()), [])
})
