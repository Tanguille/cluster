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
-- 2. Dead swarms: DHT-only, always 0 seeders, first seen over 3 days ago (re-sightings update counts, so revived swarms escape).
select encode(info_hash, 'hex')
from torrents_torrent_sources
group by info_hash
having bool_and(source = 'dht')
  and count(seeders) = count(*)
  and max(seeders) = 0
  and min(created_at) < now() - interval '3 days';
