import type { BaselineTrack } from '../types.ts'
export interface TrackFilters {
  q: string; gpu: string; model: string; status: string; mine: boolean; hideRetired: boolean
}
export function filterTracks<T extends Pick<BaselineTrack, 'slug' | 'name' | 'model_name' | 'gpu_type' | 'status'>>(
  tracks: T[], filters: TrackFilters, ownedSlugs: ReadonlySet<string>,
): T[] {
  const query = filters.q.trim().toLowerCase()
  return tracks.filter(track => (!filters.status ? !filters.hideRetired || track.status !== 'retired' : track.status === filters.status)
    && (!filters.gpu || track.gpu_type === filters.gpu)
    && (!filters.model || track.model_name === filters.model)
    && (!filters.mine || ownedSlugs.has(track.slug))
    && (!query || [track.name, track.model_name, track.gpu_type].join(' ').toLowerCase().includes(query)))
}
