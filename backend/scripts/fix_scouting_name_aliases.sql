-- Fix normalized_name in wc2026_scouting_stats for players whose names differ
-- between ScoutingStats and wc2026_squad_players.
--
-- ScoutingStats uses Given-Family order for Korean names; squad uses Family-Given.
-- Some players have nicknames, abbreviated names, or full-name variants.
-- We update normalized_name to match the broken-but-consistent SQL norm used
-- in wc2026_squad_players: lower(regexp_replace(unaccent(name), '[^a-z0-9 ]', '', 'g'))

BEGIN;

-- === Korean names (Given-Family in SC → Family-Given in squad) ===
UPDATE wc2026_scouting_stats SET normalized_name = 'on eungmin'    WHERE player_name = 'Heung-min Son'     AND season = '2025-2026';
UPDATE wc2026_scouting_stats SET normalized_name = 'ee angin'      WHERE player_name = 'Kang-in Lee'       AND season = '2025-2026';
UPDATE wc2026_scouting_stats SET normalized_name = 'wang nbeom'    WHERE id = (SELECT id FROM wc2026_scouting_stats WHERE player_name ILIKE 'In-beom Hwang%' AND season = '2025-2026' LIMIT 1);
UPDATE wc2026_scouting_stats SET normalized_name = 'ee aesung'     WHERE player_name = 'Jae-sung Lee'      AND season = '2025-2026';
UPDATE wc2026_scouting_stats SET normalized_name = 'ang yunjun'    WHERE player_name = 'Hyun-jun Yang'     AND season = '2025-2026';
UPDATE wc2026_scouting_stats SET normalized_name = 'ho uesung'     WHERE player_name = 'Gue-sung Cho'      AND season = '2025-2026';
UPDATE wc2026_scouting_stats SET normalized_name = 'wang eechan'   WHERE player_name = 'Hee-chan Hwang'    AND season = '2025-2026';
UPDATE wc2026_scouting_stats SET normalized_name = 'im oonhwan'    WHERE player_name = 'Moon-hwan Kim'     AND season = '2025-2026';
UPDATE wc2026_scouting_stats SET normalized_name = 'h yeongyu'     WHERE player_name = 'Hyeon-gyu Oh'      AND season = '2025-2026';
UPDATE wc2026_scouting_stats SET normalized_name = 'ae unho'       WHERE player_name = 'Jun-ho Bae'        AND season = '2025-2026';
UPDATE wc2026_scouting_stats SET normalized_name = 'ho ije'        WHERE player_name = 'Wi-je Cho'         AND season = '2025-2026';
UPDATE wc2026_scouting_stats SET normalized_name = 'om isung'      WHERE player_name = 'Ji-sung Eom'       AND season = '2025-2026';
UPDATE wc2026_scouting_stats SET normalized_name = 'o yeonwoo'     WHERE player_name = 'Hyeon-woo Jo'      AND season = '2025-2026';
UPDATE wc2026_scouting_stats SET normalized_name = 'im ingyu'      WHERE player_name = 'Jin-gyu Kim'       AND season = '2025-2026';
UPDATE wc2026_scouting_stats SET normalized_name = 'ee onggyeong'  WHERE player_name = 'Dong-gyeong Lee'   AND season = '2025-2026';
UPDATE wc2026_scouting_stats SET normalized_name = 'ee anbeom'     WHERE player_name = 'Han-beom Lee'      AND season = '2025-2026';
UPDATE wc2026_scouting_stats SET normalized_name = 'ee aeseok'     WHERE player_name = 'Tae-seok Lee'      AND season = '2025-2026';
UPDATE wc2026_scouting_stats SET normalized_name = 'aik eungho'    WHERE player_name = 'Seung-ho Paik'     AND season = '2025-2026';
UPDATE wc2026_scouting_stats SET normalized_name = 'ark inseob'    WHERE player_name = 'Jin-seob Park'     AND season = '2025-2026';
UPDATE wc2026_scouting_stats SET normalized_name = 'eol oungwoo'   WHERE player_name = 'Young-woo Seol'    AND season = '2025-2026';
UPDATE wc2026_scouting_stats SET normalized_name = 'ong umkeun'    WHERE player_name = 'Bum-keun Song'     AND season = '2025-2026';
UPDATE wc2026_scouting_stats SET normalized_name = 'im aehyeon'    WHERE player_name = 'Kim Tae-Hyeon'     AND season = '2025-2026';

-- === Non-Korean aliases ===
-- Full name vs nickname/abbreviated name
UPDATE wc2026_scouting_stats SET normalized_name = 'lex rimaldo'         WHERE player_name = 'Alejandro Grimaldo'   AND season = '2025-2026';
UPDATE wc2026_scouting_stats SET normalized_name = 'ose imenez'          WHERE player_name = 'José María Giménez'   AND season = '2025-2026';
UPDATE wc2026_scouting_stats SET normalized_name = 'derson oraes'        WHERE player_name = 'Ederson'              AND season = '2025-2026' AND stats->>'team_name' = 'Fenerbahçe';
UPDATE wc2026_scouting_stats SET normalized_name = 'bdul atawu'          WHERE player_name = 'Issahaku Fatawu'      AND season = '2025-2026';
UPDATE wc2026_scouting_stats SET normalized_name = 'anilo uiz'           WHERE player_name = 'Danilo'               AND season = '2025-2026' AND stats->>'team_name' = 'Flamengo';
UPDATE wc2026_scouting_stats SET normalized_name = 'anilo antos'         WHERE player_name = 'Danilo'               AND season = '2025-2026' AND stats->>'team_name' = 'Botafogo';
UPDATE wc2026_scouting_stats SET normalized_name = 'aximiliano raujo'    WHERE player_name = 'Maxi Araújo'          AND season = '2025-2026';
UPDATE wc2026_scouting_stats SET normalized_name = 'arcus olmgren edersen' WHERE player_name = 'Marcus Pedersen'   AND season = '2025-2026' AND stats->>'team_name' = 'Torino';
UPDATE wc2026_scouting_stats SET normalized_name = 'redrik ndre jrkan'   WHERE player_name = 'Fredrik Bjørkan'     AND season = '2025-2026';
UPDATE wc2026_scouting_stats SET normalized_name = 'awrence tiigi'       WHERE player_name = 'Lawrence Ati Zigi'   AND season = '2025-2026';
UPDATE wc2026_scouting_stats SET normalized_name = 'lejandro endejas'    WHERE player_name = 'Álex Zendejas'       AND season = '2025-2026';
UPDATE wc2026_scouting_stats SET normalized_name = 'uc de ougerolles'    WHERE player_name = 'Luc De Fougerolles'  AND season = '2025-2026';
UPDATE wc2026_scouting_stats SET normalized_name = 'omoki ayakawa'       WHERE player_name = 'T. Hayakawa'         AND season = '2025-2026';
UPDATE wc2026_scouting_stats SET normalized_name = 'athan aliba'         WHERE player_name = 'Nathan-Dylan Saliba' AND season = '2025-2026';

COMMIT;
