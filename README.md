# Rovdata Ulv – Home Assistant integrasjon

HACS-integrasjon som henter ulveobservasjoner og utbredelsesområder inn i Home Assistant, med egne entiteter per individ/observasjon og kartvisning.

## Datakilder

| Kilde | Innhold |
|---|---|
| [Rovbase](https://www.rovbase.no) | DNA-prøver, observasjoner og skader – med navn på hvert individ |
| [GBIF / Skandobs](https://www.gbif.org/dataset/9ea87732-b88e-488d-a02b-3dc6e9b885e0) | Ulveobservasjoner med koordinater, dato og observatør |
| [Miljødirektoratet ArcGIS](https://kart.miljodirektoratet.no/arcgis/rest/services/sensitive_artsdata/sensitive_artsdata_maskering/MapServer/29) | Maskerte utbredelsesområder (10×10 km rutenett, krever token) |

## Entiteter

For hver observasjon/område opprettes to entiteter:

- **`device_tracker.rovdata_*`** – vises som prikk på kartet i Home Assistant
- **`sensor.rovdata_*`** – dato for siste observasjon med alle detaljer som attributter

Rovbase-entiteter navngis med individets ID og navn, f.eks. `Ulv UI418779 G88-23 V1105`.

## Krav

- Home Assistant 2024.1 eller nyere
- [HACS](https://hacs.xyz) installert

## Installasjon via HACS

1. Gå til **HACS → Integrasjoner → ⋮ → Custom repositories**
2. Lim inn `https://github.com/jantoretelnes/hacs-rovdata` og velg kategori **Integration**
3. Klikk **Last ned**
4. Start Home Assistant på nytt
5. Gå til **Innstillinger → Integrasjoner → Legg til integrasjon** og søk etter *Rovdata Ulv*

## Konfigurasjon

### Soner

Opprett soner i Home Assistant med navn som starter med `rovdata_`. Integrasjonen finner dem automatisk.

Eksempel:
- `rovdata_østfold`
- `rovdata_hedmark`
- `rovdata_akershus`

Soner opprettes under **Innstillinger → Områder og soner → Soner**.

### Innstillinger

| Felt | Standard | Beskrivelse |
|---|---|---|
| Maks alder på observasjoner (dager) | 365 | Hvor gamle observasjoner som hentes |
| ArcGIS-token (valgfritt) | — | Token fra Miljødirektoratet for tilgang til maskerte utbredelsesområder |

Innstillingene kan endres i ettertid via **Integrasjoner → Rovdata Ulv → Konfigurer**.

### ArcGIS-token

Token kan skaffes ved henvendelse til [Miljødirektoratet](https://www.miljodirektoratet.no). Uten token hentes kun Rovbase- og GBIF-data.

## Oppdatering

Data hentes automatisk én gang per døgn. Manuell oppdatering gjøres via **Integrasjoner → Rovdata Ulv → Hent på nytt**.

## Attributter

### Rovbase-observasjoner

| Attributt | Beskrivelse |
|---|---|
| `kilde` | Rovbase |
| `individ_id` | Individets ID (f.eks. UI418779) |
| `individ_navn` | Individets navn (f.eks. G88-23 V1105) |
| `dato` | Dato for siste observasjon |
| `lokalitet` | Funnsted |
| `kommune` | Kommune |
| `datatype` | Type observasjon (dna, Rovviltobservasjon, osv.) |
| `dna_id` | DNA-prøve-ID |
| `sone` | HA-sone observasjonen tilhører |

### GBIF-observasjoner

| Attributt | Beskrivelse |
|---|---|
| `kilde` | GBIF / Skandobs |
| `occurrence_id` | Unik ID fra GBIF |
| `dato` | Dato for observasjonen |
| `lokalitet` | Stedsnavn |
| `fylke` | Fylke |
| `antall_individer` | Antall ulv i observasjonen |
| `registrert_av` | Observatør |
| `datasett` | Navn på datasett (f.eks. Skandobs) |
| `merknader` | Fritekst fra observatør |
| `sone` | HA-sone observasjonen tilhører |

### ArcGIS-utbredelsesområder

| Attributt | Beskrivelse |
|---|---|
| `kilde` | ArcGIS / Miljødirektoratet |
| `maskeringsrute_id` | ID på rutenettet |
| `art` | Artsnavn (Ulv) |
| `vitenskapelig_navn` | Canis lupus |
| `datasett` | Killedatasett |
| `institusjon` | Ansvarlig institusjon |
| `sone` | HA-sone området tilhører |

## Lisens

MIT
