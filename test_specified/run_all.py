"""依次运行指定航程（fare_plan）四类测试，场景间冷却以降低风控。"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = [
    ("国内单程", "test_specified/domestic_oneway.yaml"),
    ("国内往返", "test_specified/domestic_round.yaml"),
    ("海外单程", "test_specified/intl_oneway.yaml"),
    ("海外往返", "test_specified/intl_round.yaml"),
]
COOLDOWN_S = 180  # 场景间隔 3 分钟（去哪儿 httpx 约 5 分钟限 1 次）


def run_one(name: str, cfg: str) -> int:
    print(f"\n{'=' * 60}\n>>> 开始: {name} ({cfg})\n{'=' * 60}")
    proc = subprocess.run(
        [sys.executable, "main.py", "--once", "-c", cfg],
        cwd=str(ROOT),
    )
    return proc.returncode


def main():
    results = []
    for i, (name, cfg) in enumerate(SCENARIOS):
        code = run_one(name, cfg)
        results.append((name, code))
        if i + 1 < len(SCENARIOS):
            print(f"\n--- 冷却 {COOLDOWN_S}s 后进入下一场景 ---\n")
            time.sleep(COOLDOWN_S)

    print("\n" + "=" * 60)
    print("测试汇总")
    for name, code in results:
        print(f"  {'✅' if code == 0 else '❌'} {name} (exit={code})")
    failed = [n for n, c in results if c != 0]
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
