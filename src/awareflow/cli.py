import argparse
import json
import time

from .bootstrap import build
from .config import Config
from .demo import run_demo, seed_users
from .domain.errors import WorkflowError


def main():
    parser = argparse.ArgumentParser(description="AwareFlow Agent and training services")
    parser.add_argument("command", choices=["init-db", "demo", "worker-once", "scheduler-once", "worker", "scheduler", "check-llm"])
    parser.add_argument("--interval", type=int, default=5, help="Polling interval in seconds")
    args = parser.parse_args()
    try:
        config = Config.from_env()
        if args.command == "check-llm":
            from .application.diagnostics import check_llm
            from .bootstrap import Clock
            try:
                print(json.dumps(check_llm(config, Clock()), ensure_ascii=False, indent=2))
            except WorkflowError:
                raise
            except Exception:
                parser.exit(1, "โมเดลไม่ผ่านการตรวจ JSON/Agent contract ตรวจการตั้งค่าและรูปแบบ API (ไม่แสดง raw response)\n")
        elif args.command == "demo":
            result = run_demo(config)
            m = result["report"]["metrics"]
            print(json.dumps({"round_id": result["round_id"], "note": result["note"],
                              "assigned": m["assigned"], "completed": m["completed"], "passed": m["passed"],
                              "average_score": m["average_score"],
                              "pdf": str(config.data_root / result["report"]["pdf_path"]),
                              "csv": str(config.data_root / result["report"]["csv_path"])}, ensure_ascii=False, indent=2))
        else:
            services = build(config)
            if args.command == "init-db":
                from .bootstrap import Clock
                seed_users(services, Clock().now())
                print("Initialized demo database and users; existing data preserved.")
            elif args.command in {"worker", "scheduler"}:
                if not 1 <= args.interval <= 3600:
                    parser.error("interval must be 1-3600")
                try:
                    while True:
                        if args.command == "worker":
                            job = services.worker.run_once()
                            if job:
                                print(json.dumps({"job_id": job["id"], "status": job["status"]}), flush=True)
                        else:
                            created = services.scheduling.tick()
                            if created:
                                print(json.dumps(created), flush=True)
                        time.sleep(args.interval)
                except KeyboardInterrupt:
                    return
            elif args.command == "scheduler-once":
                print(json.dumps(services.scheduling.tick()))
            else:
                job = services.worker.run_once()
                print(json.dumps({"job_id": job["id"], "status": job["status"]} if job else {"status": "IDLE"}))
    except WorkflowError as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
