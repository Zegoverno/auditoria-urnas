# Fase 3 — final result (whole country, 2026 election, both rounds)

Run: 2026-10-30 15:38 UTC → 2026-10-31 00:44 UTC (9.1 h wall, 7.6 h of processing; includes the
SP 1t OOM crash and restart). 56/56 UF-rounds, 219.2 GB of TSE zips, 998,414 sections.
Lake: 5.0 GB. Raw files of the sections that diverge: `data/raw/arqurnatot_problemas/` (4.7 MB).
Full check run: `duckdb < checagens/fase3.sql` (with memory_limit 2GB, 2 threads), 2026-10-31.

| Check | Country total | Diverging |
|---|---|---|
| U1 file hash × signed SW/HW lists | 3,993,655 files | 1 file (MG 1t) |
| U2 RDV = BU (section × office × candidate) | 69,950,925 rows | 71 rows, 1 section (MA 1t) |
| U3 binary BU = CSV BU (President) | 5,731,614 rows | 0 |
| U4 log votes = turnout | 253,509,730 voters; 253,509,653 in logs | 17 sections, 13 UF-rounds |
| U5 biometrics + birth year + no biometrics = turnout | 253,509,730 | 0 |
| U6 zip sections × CSV sections | 998,413 in both | 1 only in zip (ZZ 1t) |
| U7 how voters were cleared, log = BU | 998,414 sections | 17 (the same as U4) |
| U4b diagnosis of the 17 U4 sections | 9 explained automatically | 8 real gaps in the log |

No vote count disagrees anywhere: in every section with a finding, the BU and the RDV agree (U2),
except MA 1t, where the RDV is missing.

## Findings

U4b explains these automatically:
- **A vote cut off by an urn restart (8 sections, 1 vote each):** RN 1t, ES 1t, PI 2t, PB 2t,
  BA 2t 34711/0021/0178, and SP 1t 63614/0227/0305, 64254/0291/0346 and 71072/0260/0037. The voter
  confirmed President, and the urn rebooted before it logged "computado". The vote is in the BU
  and the RDV.
- **Another section's log (1 section):** ES 2t 0085. A reused contingency urn carries section
  0112's log (304 votes) inside its own. No votes differ.

Real gaps in the published log (8 sections; BU = RDV in each):
- **MA 2t 0889/0047/0609:** 36 of 224 votes are missing from the log. The original urn's log
  (08:00–~10:30) is absent; contingency urn 01664037 logged 188 votes.
- **RS 1t 89036/0083/0044:** 1 of 279 votes is missing. The GAP error said the media and the urn
  disagreed on turnout (91 vs 90).
- **BA 1t 39934/0183/0175:** 136 of 272 votes are missing. The voting media went into section
  0181's urn, which wrote 0181's log onto it; the 08:00–13:19 log is absent.
- **BA 2t 35653/0173/0235:** 13 of 217 votes are missing. They were cast on intermediate urn
  01681986, whose own log was not published.
- **RJ 1t 58971/0087/0184:** 2 of 258 votes are missing. The UE2020 urn was unstable and rebooted
  about 20 times.
- **RS 2t 89060/0017/0191:** 2 votes are missing, during a gap in the log from 09:02 to 09:38.
- **SP 1t 71072/0002/0035 (reservaSecao):** 15 of 247 votes are missing. The file holds only a
  failed 10:18 boot of urn 02254720 (GAP error 72: the media and the urn don't match) and
  contingency urn 02253783 from 10:58, which logged 232 votes. The log of the original urn
  (08:00–~10:18) is absent.
- **SP 2t 64777/0395/0298:** no log.jez was published for this section; 268 voters have no log.
  BU, RDV and signatures are fine.

Other findings:
- **MA 1t 07846/0074/0228 (U2):** no rdv.dat in the TSE zip, so its 219 voters can't be
  recounted. The BU, the log and the signatures are fine.
- **MG 1t 50075/0047/0041 (U1):** a counting-board (Sistema de Apuração) section that also
  publishes the urn's own imgbu.dat. That file is not in the signed lists. The urn's BU has 258
  votes; the signed board BU has 278 and matches the CSV. Probably mixed voting with paper
  ballots, but this is not verified.
- **ZZ 1t 29564/0001/0091 (Ciudad Guayana, U6):** a contingency file with 0 turnout. The section
  is not in the 1st-round CSV but is in the 2nd-round one.

Fixes made during the run: f6473c3 (free-numbered local ballot question, MT 2t), db3edb4 and
8eaeb58 (U4b), 7e048fe (DuckDB memory cap after SP 1t took the VM down).
