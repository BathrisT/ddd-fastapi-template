"""Прямые присваивания и replace разрешены владельцу объекта и юзкейсу."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ownership_flow import Flow
from _ownership_model import Origin, Program, Summary, Value


def _refine_fields(program: Program, summaries: dict[str, Summary]) -> bool:
    changed = False
    for key, summary in summaries.items():
        function = program.functions[key]
        if function.node.name != "__init__":
            continue
        known = program.fields[function.owner.owner]
        for name, value in summary.fields:
            binding = Value(
                frozenset({Origin(f"{function.owner.owner}:field:{name}", "parameter")})
            )
            parameters = {
                origin.owner: binding for origin in value.origins if origin.role == "parameter"
            }
            refined = value.bind(parameters)
            if known.get(name) != refined:
                known[name] = refined
                changed = True
    return changed


def check_object_ownership() -> list[str]:
    program = Program()
    program.load()
    # Итерации нужны для цепочек возвратов независимо от порядка файлов.
    for _ in range(max(4, len(program.functions) * 2 + 1)):
        summaries = {
            key: Flow(program, function).run() for key, function in program.functions.items()
        }
        fields_changed = _refine_fields(program, summaries)
        if summaries == program.summaries and not fields_changed:
            break
        program.summaries = summaries
    else:
        raise ValueError("Анализ происхождения не сошёлся: проверьте рекурсивные вызовы")
    called = set().union(*(summary.calls for summary in summaries.values())) if summaries else set()
    errors: set[str] = set()
    for key, summary in summaries.items():
        for write in summary.writes:
            for origin in write.value.origins or {None}:
                if origin and origin.role == "parameter":
                    if key in called:
                        continue
                    reason = "неизвестное происхождение аргумента"
                elif (
                    origin
                    and origin == write.writer
                    or (
                        origin
                        and write.writer.role == "usecase"
                        and origin.role in {"infrastructure", "application", "domain"}
                    )
                ):
                    continue
                else:
                    reason = (
                        f"происхождение `{origin.owner}` ({origin.role})"
                        if origin
                        else "неизвестное происхождение"
                    )
                errors.add(
                    f"{write.path}:{write.line}: присваивание/replace запрещено: {reason}; изменяет `{write.writer.owner}`"
                )
    return sorted(errors)


def main() -> int:
    try:
        errors = check_object_ownership()
    except (OSError, SyntaxError, UnicodeError, ValueError) as exc:
        print(f"ОШИБКА АНАЛИЗА: {exc}")
        return 2
    for error in errors:
        print(error)
    if not errors:
        print("Object ownership: прямые присваивания и replace проверены. OK.")
    return int(bool(errors))


if __name__ == "__main__":
    sys.exit(main())
