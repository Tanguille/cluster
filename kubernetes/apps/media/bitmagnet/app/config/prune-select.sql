-- Hashes to delete and blocklist:
-- 1. TMDB-matched foreign dubs: no English, Dutch or original-language track, and an explicit
--    language tag in the name (the parser alone misreads words like "Spa" or "Cat"). Subbed releases stay.
select encode(info_hash, 'hex')
from (
  select t.info_hash, regexp_replace(lower(t.name), '[^a-z0-9]+', ' ', 'g') as words
  from torrents t
  join torrent_contents tc using (info_hash)
  join content c on c.source = tc.content_source and c.id = tc.content_id
  where tc.content_type in ('movie', 'tv_show')
    and c.original_language is not null
    and jsonb_array_length(tc.languages) > 0
    and not (tc.languages ?| array['en', 'nl', c.original_language])
) d
where words ~ '\m(rus(sian)?|rudub|ukr(ainian)?|(true)?french|vf[fqi2]?|ita(lian)?|ger(man)?|deutsch|spanish|castellano|latino|esp|pol(ish)?|pl|lektor|cze(ch)?|hun(garian)?|tur(kish)?|por(tuguese)?|dublado|hindi|tamil|telugu|jap(anese)?|kor(ean)?|chinese|mandarin|swe(dish)?|norwegian|danish|finnish|ara(bic)?|heb(rew)?|greek)\M'
  and words !~ '\m(en|eng)?subs?(bed)?\M'
union
-- 2. Weak swarms, DHT-only with known counts: at most 1 seeder after 3 days, at most 5 after 180 days
--    (re-sightings update counts, so revived swarms escape).
select encode(info_hash, 'hex')
from torrents_torrent_sources
group by info_hash
having bool_and(source = 'dht')
  and count(seeders) = count(*)
  and ((max(seeders) <= 1 and min(created_at) < now() - interval '3 days')
    or (max(seeders) <= 5 and min(created_at) < now() - interval '180 days'))
union
-- 3. Still no TMDB match after 90 days (bitmagnet has no retention of its own). torrents.created_at,
--    because a reprocess rewrites torrent_contents rows.
select encode(t.info_hash, 'hex')
from torrents t
where t.created_at < now() - interval '90 days'
  and not exists (select 1 from torrent_contents tc where tc.info_hash = t.info_hash and tc.content_id is not null);
