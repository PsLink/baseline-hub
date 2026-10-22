import { auth } from '../../client.ts'

/** Display hint only. The API independently verifies the numeric campaign owner. */
export function ownsSubmission(entry: { created_by_name?: string; submitted_by?: string }) {
  const username = auth.upstream?.username
  return !!username && username === (entry.created_by_name ?? entry.submitted_by)
}
