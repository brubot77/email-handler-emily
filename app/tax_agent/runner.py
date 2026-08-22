from __future__ import annotations
import argparse, json
from pathlib import Path
from .core import build_candidates
from .models import TaxRecord
from .parser import parse_foreclosure_exhibit
from .sources import collect_live_records
from .tracker import write_tracker

def _record_from_json(raw:dict)->TaxRecord:
    return TaxRecord(county=raw["county"],parcel_id=str(raw.get("parcel_id","")),tax_id=str(raw.get("tax_id","")),address=raw.get("address",""),city=raw.get("city",""),state=raw.get("state","KS"),zip_code=str(raw.get("zip_code","")),owner=raw.get("owner",""),delinquent_years=tuple(raw.get("delinquent_years",())),amount_due=raw.get("amount_due"),appraised_value=raw.get("appraised_value"),property_class=raw.get("property_class",""),status=raw.get("status","ACTIVE"),source_url=raw.get("source_url",""),source_type=raw.get("source_type",""),notes=raw.get("notes",""))

def main():
    parser=argparse.ArgumentParser(description="BLU delinquent real-estate tax agent")
    parser.add_argument("--input-json")
    parser.add_argument("--exhibit-text")
    parser.add_argument("--live-source",action="store_true",help="Read official county sources. Does not imply tracker write.")
    parser.add_argument("--county",action="append",help="County for live source; repeatable. Defaults to all.")
    parser.add_argument("--source-url",default="")
    parser.add_argument("--tracker",default="tax_agent_output/BLU_Delinquent_Tax_Tracker.csv")
    parser.add_argument("--min-years",type=int,default=2)
    parser.add_argument("--max-value",type=float,default=130000)
    parser.add_argument("--verified-values-only",action="store_true")
    parser.add_argument("--dry-run",action="store_true",help="Print candidates; do not write tracker")
    parser.add_argument("--print-limit",type=int,default=60,help="Maximum candidates to print; 0 prints all")
    args=parser.parse_args()

    records=[]
    if args.input_json:
        raw=json.loads(Path(args.input_json).read_text(encoding="utf-8"))
        records.extend(_record_from_json(item) for item in raw)
    if args.exhibit_text:
        text=Path(args.exhibit_text).read_text(encoding="utf-8")
        exhibit_county=(args.county or ["Sedgwick"])[0]
        records.extend(parse_foreclosure_exhibit(text,county=exhibit_county,source_url=args.source_url))
    if args.live_source:
        live,audit=collect_live_records(set(args.county) if args.county else None)
        records.extend(live)
        print("Live-source audit:")
        for county,url,count,status in audit:
            print(f"  {county:<9} rows={count:<3} {status:<25} {url}")
    if not records:
        parser.error("Provide --input-json, --exhibit-text, or --live-source.")

    candidates=build_candidates(records,min_years=args.min_years,max_value=args.max_value,include_unknown_value=not args.verified_values_only)
    counts={}
    addressed=0
    for c in candidates:
        counts[c.record.county]=counts.get(c.record.county,0)+1
        addressed += 1 if c.record.address else 0
    county_summary=", ".join(f"{k}={v}" for k,v in sorted(counts.items()))
    print(f"Candidate summary: total={len(candidates)}, with_street_address={addressed}" + (f", {county_summary}" if county_summary else ""))
    shown=candidates if args.print_limit == 0 else candidates[:max(args.print_limit,0)]
    for i,c in enumerate(shown,1):
        r=c.record
        print(f"{i:>3}. {c.score:>3} | {r.county:<9} | {r.address or '[NO ADDRESS]'} | parcel={r.parcel_id or '-'} | years={','.join(map(str,r.delinquent_years))} | {c.foreclosure_stage}")
    if args.print_limit > 0 and len(candidates) > len(shown):
        print(f"... {len(candidates)-len(shown)} additional candidate(s) omitted; use --print-limit 0 to show all.")
    if args.dry_run:
        print(f"Dry run: {len(candidates)} candidate(s); tracker not modified.")
        return
    if args.live_source and not args.verified_values_only:
        parser.error(
            "Refusing live-source tracker write without --verified-values-only. "
            "County appraisal/value enrichment must be completed before raw live candidates are written."
        )
    path=write_tracker(args.tracker,candidates)
    print(f"Wrote {len(candidates)} candidate(s) to {path}")

if __name__=="__main__":main()
