-- Best-effort: prune-select.sql runs in the same psql session, so a failed drop must not stop it.
-- psql autocommits each statement, so CONCURRENTLY works.
\set ON_ERROR_STOP off
-- bitmagnet purges processed jobs after 7 days (hardcoded); keep 1, their payloads are never read again.
delete from queue_jobs where status = 'processed' and ran_at < now() - interval '1 day';

-- Only the web UI sorts or filters on these; the crawler, Torznab, classifier and prune never scan them.
-- Up migrations drop indexes IF EXISTS, so this is upgrade-safe. Re-check this list after bitmagnet bumps:
-- a new migration that re-creates one of these names to serve a query would be dropped again here, silently.
drop index concurrently if exists queue_jobs_queue_payload_idx;
drop index concurrently if exists torrent_tags_name_idx1;
drop index concurrently if exists torrent_files_size_idx;
drop index concurrently if exists torrents_name_idx;
drop index concurrently if exists torrents_size_idx;
drop index concurrently if exists torrents_files_status_idx;
drop index concurrently if exists torrents_torrent_sources_created_at_idx;
drop index concurrently if exists torrents_torrent_sources_seeders_idx;
drop index concurrently if exists torrents_torrent_sources_leechers_idx;
drop index concurrently if exists torrents_torrent_sources_import_id_idx;
drop index concurrently if exists torrent_contents_size_idx;
drop index concurrently if exists torrent_contents_episodes_idx;
drop index concurrently if exists torrent_contents_release_group_idx;
drop index concurrently if exists torrent_contents_coalesce_idx;
drop index concurrently if exists torrent_contents_seeders_idx;
drop index concurrently if exists torrent_contents_seeders_coalesce_idx;
drop index concurrently if exists torrent_contents_leechers_idx;
drop index concurrently if exists torrent_contents_leechers_coalesce_idx;
drop index concurrently if exists torrent_contents_video_codec_idx;
drop index concurrently if exists torrent_contents_video_3d_idx;
drop index concurrently if exists torrent_contents_video_modifier_idx;
drop index concurrently if exists torrent_contents_content_source_idx;
drop index concurrently if exists content_attributes_key_idx;
drop index concurrently if exists content_attributes_source_key_idx;
drop index concurrently if exists content_popularity_idx;
\set ON_ERROR_STOP on
