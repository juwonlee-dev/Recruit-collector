from __future__ import annotations

import argparse
import logging
import os

from recruit_collector.config import load_config
from recruit_collector.models import make_session_factory
from recruit_collector.runner import TRIGGER_CLI, run_all


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="recruit-collector")
    parser.add_argument("-c", "--config", default="config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    once = sub.add_parser("run-once", help="설정된 검색 조건을 1회 수집")
    once.add_argument("--search", action="append", help="특정 검색 조건 이름만 수집 (여러 번 지정 가능)")

    worker = sub.add_parser("worker", help="수집기: 자동 수집 스케줄러와 조회 화면의 '지금 수집' 요청을 처리 (하나만 띄움)")
    worker.add_argument(
        "--stop-timeout", type=float, default=float(os.environ.get("WORKER_STOP_TIMEOUT", 570)),
        help="종료 신호(SIGTERM)를 받으면 진행 중인 수집을 최대 몇 초 기다릴지 (기본 570초, 환경 변수 WORKER_STOP_TIMEOUT)",
    )

    catalog = sub.add_parser("build-catalog", help="사이트별 직무·지역 선택지를 받아 필터 생성용 매핑 데이터를 갱신")
    catalog.add_argument("--site", action="append", help="특정 사이트만 (여러 번 지정 가능)")

    serve = sub.add_parser("serve", help="적재된 공고 조회 웹 화면 실행")
    serve.add_argument("--host", default="0.0.0.0")  # 모든 네트워크에서 접속 허용 (이 PC에서만: --host 127.0.0.1)
    serve.add_argument("--port", type=int, default=8000)

    sub.add_parser("login-off", help="조회 화면의 로그인 사용을 끈다 (관리자로 로그인할 수 없게 된 경우 복구용)")

    migrate = sub.add_parser("migrate-sqlite", help="SQLite DB의 모든 데이터를 설정의 DB(PostgreSQL 등)로 옮긴다")
    migrate.add_argument("source", help="원본 SQLite 파일 (예: data/recruit.db). 읽기 전용으로 연다")
    migrate.add_argument("--replace", action="store_true", help="대상 DB에 공고가 있어도 비우고 다시 옮긴다")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    config = load_config(args.config)
    session_factory = make_session_factory(config.database_url)

    if args.command == "run-once":
        run_all(config, session_factory, only=set(args.search) if args.search else None, trigger=TRIGGER_CLI)
        return

    if args.command == "migrate-sqlite":
        from recruit_collector import migrate as migrate_mod

        src, dst = migrate_mod.sqlite_readonly(args.source), session_factory.kw["bind"]
        print(f"원본: {src.url.database}")
        print(f"대상: {dst.url.render_as_string(hide_password=True)}")
        counts = migrate_mod.copy_all(src, dst, replace=args.replace)
        for name, n in counts.items():
            print(f"  {name}: {n}행")
        problems = migrate_mod.verify(src, dst)
        if problems:
            raise SystemExit("검증 실패: " + " / ".join(problems))
        print("검증 완료: 모든 표의 모든 행이 원본과 같습니다.")
        return

    if args.command == "login-off":
        from recruit_collector.web import auth

        with session_factory() as s:
            auth.set_setting(s, auth.LOGIN_ENABLED_KEY, "0")
            s.commit()
        print("로그인 사용을 껐습니다. 모든 기능을 제한 없이 쓸 수 있습니다.")
        return

    if args.command == "build-catalog":
        from recruit_collector import filters, regions

        for site in args.site or config.sites():
            data = filters.build_catalog(config, site)
            print(f"{site}: 직무 선택지 {len(data['options'])}개 -> {filters.catalog_path(config, site)}")
            data = regions.build_catalog(config, site)
            print(f"{site}: 지역 선택지 {len(data['options'])}개 -> {regions.catalog_path(config, site)}")
        return

    if args.command == "serve":
        import uvicorn

        from recruit_collector.web.app import create_app

        uvicorn.run(create_app(session_factory, config), host=args.host, port=args.port)
        return

    if args.command == "worker":
        from recruit_collector.worker import run_worker

        run_worker(config, session_factory, stop_timeout=args.stop_timeout)
        return


if __name__ == "__main__":
    main()
