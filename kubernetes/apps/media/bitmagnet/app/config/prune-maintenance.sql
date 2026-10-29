-- Housekeeping before prune-select.sql. psql runs each statement in autocommit, so CONCURRENTLY works.
-- bitmagnet archives processed jobs for 7 days (hardcoded); their payloads are pure dead weight after a day.
delete from queue_jobs where status = 'processed' and ran_at < now() - interval '1 day';

-- Indexes bitmagnet's migrations create that neither the crawler, Torznab, the classifier nor prune use:
-- 0 scans in 51 h on 2026-10-29 (the size, name and files_status ones: 2-3, from manual queries).
-- Only the web UI sorts or filters on these columns. Migrations drop indexes with IF EXISTS, so this is upgrade-safe.
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
