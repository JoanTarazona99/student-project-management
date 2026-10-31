from pathlib import Path
from importlib.metadata import version
import secrets

from dotenv import dotenv_values, set_key


def main():
    root = Path(__file__).resolve().parents[1]
    env = root / ".env"
    if not env.exists():
        raise SystemExit("ОШИБКА: сначала создайте локальный файл .env.")
    values = dotenv_values(env, interpolate=False)
    secret = values.get("JWT_SECRET") or ""
    if secret and len(secret) < 32:
        raise SystemExit("ОШИБКА: JWT_SECRET короче 32 символов. Проверьте настройку.")
    if not secret:
        set_key(str(env), "JWT_SECRET", secrets.token_urlsafe(48))
    example = root / ".env.example"
    if example.exists():
        if "JWT_SECRET" not in dotenv_values(example, interpolate=False):
            with example.open("a", encoding="utf-8") as stream:
                stream.write("\nJWT_SECRET=\n")
    requirements = root / "requirements.txt"
    lines = requirements.read_text(encoding="utf-8").splitlines()
    lines = [line for line in lines if not line.lower().startswith("pyjwt")]
    lines.append("PyJWT==" + version("PyJWT"))
    requirements.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Настройка JWT завершена. Секрет сохранен только в локальном .env.")
    print("requirements.txt обновлен. Существующий секрет не изменен.")


if __name__ == "__main__":
    main()
