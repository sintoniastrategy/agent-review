from contextlib import contextmanager
from pathlib import Path
import uuid

from . import ReviewError, now, read_json, write_json
from .documents import locked, read_document, write_document, validate_finding
from . import names


ACTIVE = {"preparing", "prepared", "starting", "running", "cancelling"}
PRIORITIES = {value: rank for rank, value in enumerate(("P0", "P1", "P2", "P3", "info", "unclassified"))}


def local_directory(repo):
    path = Path(repo) / ".agr"
    if path.is_symlink():
        raise ReviewError(".agr must be a real directory inside the worktree")
    path.mkdir(mode=0o700, exist_ok=True)
    ignore = path / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n", encoding="utf-8")
    return path


class Journal:
    def __init__(self, directory, readonly=False):
        self.directory = Path(directory).resolve()
        self.readonly = readonly
        self.manifest = read_json(self.directory / "session.json")
        if self.manifest.get("schema") != 4:
            raise ReviewError("Unsupported journal schema: " + str(self.manifest.get("schema")))

    @classmethod
    def create(cls, repo, base, task):
        if not task.strip():
            raise ReviewError("Describe the task and its scope")
        directory = local_directory(repo)
        with locked(directory / ".lock"):
            if (directory / "session.json").exists():
                raise ReviewError("This worktree already has a review session")
            write_json(directory / "session.json", {
                "schema": 4, "id": uuid.uuid4().hex[:12], "repo": str(repo),
                "base": base, "task": task, "created_at": now(),
            })
        return cls(directory)

    @classmethod
    def resolve(cls, repo, selector=None):
        journal = cls(Path(repo) / ".agr")
        if selector and selector != journal.manifest['id']:
            raise ReviewError("Unknown session: " + selector)
        return journal

    @contextmanager
    def access(self, write=False):
        if self.readonly:
            if write:
                raise ReviewError('Journal is read-only in the reviewer runtime')
            yield self
            return
        with locked(self.directory / ".lock", exclusive=write):
            yield self

    def rows(self, table, access=None):
        if access is None:
            with self.access() as connection:
                return self.rows(table, connection)
        if table == 'rounds':
            return sorted((read_json(path) for path in (self.directory / 'rounds').glob('*/status.json')), key=lambda value: value['id'])
        if table == 'findings':
            return self.reviewer_records('findings', access)
        if table == 'events':
            events = [read_document(path) for path in sorted((self.directory / 'decisions').glob('*.md'))]
            events += self.reviewer_records('events', access)
            return sorted(events, key=lambda value: (value['at'], str(value.get('sequence', ''))))
        raise ReviewError('Unknown journal collection')

    def reviewer_records(self, table, access):
        values = []
        for record in self.rows('rounds', access):
            directory = self.directory / 'rounds' / record['directory']
            output = directory / 'output'
            if table == 'findings':
                paths = list((output / 'findings').glob('*.md')) + list((directory / 'imports').glob('*.md'))
            else:
                paths = list((output / 'checks').glob('*.md')) + ([output / 'report.md'] if (output / 'report.md').is_file() else [])
            for path in sorted(paths):
                value = read_document(path)
                value.update(round=record['id'], reviewer=record['reviewer'])
                value.setdefault('at', record.get('report_at') if path.name == 'report.md' and record.get('report_at') else record.get('finished_at') or record['created_at'])
                values.append(value)
        return values

    def event(self, access, kind, **values):
        sequence = len(list((self.directory / 'decisions').glob('*.md'))) + 1
        data = {'kind': kind, 'at': now(), 'sequence': sequence, **values}
        suffix = '-' + values['finding'] if values.get('finding') else ''
        write_document(self.directory / 'decisions' / ('%04d-' % sequence + kind + suffix + '.md'), data)
        return data

    def state(self, access=None):
        status = "active"
        for event in self.rows("events", access):
            if event["kind"] == "session_state":
                status = event["status"]
        return status

    def new_round(self, source, runtime, model=None, effort=None, parallel_with=None, preset=None, scope="full"):
        if not isinstance(runtime, dict) or runtime.get("mode") not in {"docker", "native"}:
            raise ReviewError("Prepare a managed docker or native runtime")
        with self.access(True) as access:
            if self.state(access) != "active":
                raise ReviewError("Session is stopped; reopen it explicitly")
            rounds = self.rows("rounds", access)
            if parallel_with is None and any(item["status"] in ACTIVE for item in rounds):
                raise ReviewError("Finish the active round or use --parallel-with REVIEWER")
            anchor = self.round(parallel_with, access) if parallel_with is not None else None
            if anchor and any(anchor['source'][key] != source[key] for key in ('repo', 'head', 'base', 'merge_base', 'tree')):
                raise ReviewError('Parallel reviewers need the same source snapshot')
            if anchor and anchor['status'] not in ACTIVE:
                raise ReviewError('Parallel anchor must still be active')
            number = len(rounds) + 1
            data = {
                "id": number, "reviewer": "claude", "status": "preparing", "created_at": now(),
                "source": source, "runtime": runtime, "model": model, "effort": effort, "preset": preset, "scope": scope,
                "session_id": str(uuid.uuid4()), "cancel_requested": False,
            }
            data['pass'] = anchor['pass'] if anchor else max((item.get('pass', item['id']) for item in rounds), default=0) + 1
            data['slot'] = 1 + sum(item.get('pass') == data['pass'] for item in rounds)
            data['directory'] = names.round_name(data)
            write_json(self.directory / 'rounds' / data['directory'] / 'status.json', data)
            return data

    def round(self, number, access=None):
        for item in self.rows("rounds", access):
            if item["id"] == number:
                return item
        raise ReviewError("Unknown round: " + str(number))

    def select_round(self, address):
        names.parse_reviewer(address)
        for item in self.rows('rounds'):
            if names.round_name(item) == address:
                return item['id']
        raise ReviewError('Unknown reviewer: ' + address)

    def update_round(self, number, expected=None, **values):
        with self.access(True) as access:
            data = self.round(number, access)
            if expected is not None and data["status"] not in expected:
                raise ReviewError("Round is " + data["status"])
            data.update(values, updated_at=now())
            write_json(self.directory / "rounds" / data["directory"] / "status.json", data)
            return data

    def round_directory(self, number):
        return self.directory / "rounds" / self.round(number)["directory"]

    def require_finding(self, access, finding):
        for item in self.rows("findings", access):
            if item["id"] == finding:
                return item
        raise ReviewError("Unknown finding: " + str(finding))

    def add_finding(self, number, values, imported=None):
        validate_finding(values)
        body = values.get("body", "")
        if not isinstance(body, str) or not body.strip():
            raise ReviewError("Finding body is required")
        with self.access(True) as access:
            if imported is None:
                self.require_running(access, number)
            elif self.round(number, access)["status"] in ACTIVE:
                raise ReviewError("Wait for a terminal round before importing from its raw output")
            if values.get("related_to"):
                self.require_finding(access, values["related_to"])
            data = {**values, "round": number, "reviewer": "claude", "at": now()}
            if imported is not None:
                data['imported_by_author'] = imported
            data.setdefault("severity", "unclassified")
            data.setdefault("title", body.splitlines()[0][:160])
            record = self.round(number, access)
            root = self.directory / 'rounds' / record['directory']
            directory = root / 'imports' if imported is not None else root / 'output' / 'findings'
            with locked(root / 'output' / '.lock'):
                existing = list((root / 'imports').glob('*.md')) + list((root / 'output' / 'findings').glob('*.md'))
                index = max((names.parse_finding(path.stem)[3] for path in existing), default=0) + 1
                data['id'] = names.finding(names.round_name(record), index)
                path = directory / (data['id'] + '.md')
                write_document(path, data)
            return {**read_document(path), 'round': number, 'reviewer': 'claude'}

    def require_running(self, access, number):
        if self.round(number, access)["status"] != "running":
            raise ReviewError("Reviewer can only write to its running round")

    def reviewer_event(self, number, kind, **values):
        with self.access(True) as access:
            self.require_running(access, number)
            if kind == "verification":
                finding = self.require_finding(access, values["finding"])
                if finding["round"] >= number:
                    raise ReviewError("Only findings from earlier rounds can be rechecked")
            record = self.round(number, access)
            output = self.directory / 'rounds' / record['directory'] / 'output'
            data = {'kind': kind, 'at': now(), 'round': number, **values}
            if kind == 'report':
                path = output / 'report.md'
            else:
                path = output / 'checks' / (values['finding'] + '.md')
            write_document(path, data)
            return data

    def findings(self, access=None):
        findings = self.rows("findings", access)
        events = self.rows("events", access)
        for item in findings:
            item["decision"] = "pending"
            item["priority"] = item["severity"]
            item["fixes"] = []
            item["verifications"] = []
            for event in events:
                if event.get("finding") != item["id"] and not (event["kind"] == "batch_done" and item["id"] in event.get("findings", [])):
                    continue
                kind = event["kind"]
                if kind == "decision":
                    item["decision"] = event["action"]
                    item["reason"] = event["reason"]
                elif kind == "assessment":
                    item["priority"] = event["priority"]
                    item["assessment"] = event
                elif kind == "fixed" or (kind == "batch_done" and item["id"] in event.get("findings", [])):
                    item["fix"] = event
                    item["fixes"].append(event)
                elif kind == "verification":
                    item["verification"] = event
                    item["verifications"].append(event)
        return sorted(findings, key=lambda item: (PRIORITIES.get(item["priority"], 5), item["id"]))

    def author_event(self, kind, **values):
        with self.access(True) as access:
            self.require_finding(access, values["finding"])
            if kind == "decision" and values["action"] not in {"fix", "reject", "defer", "pending"}:
                raise ReviewError("Unknown decision")
            if kind == "assessment" and values["priority"] not in PRIORITIES:
                raise ReviewError("Unknown priority")
            if not values.get("reason", "").strip():
                raise ReviewError("A reason is required")
            return self.event(access, kind, **values)

    def decide(self, identifiers, action, reason):
        if not identifiers or len(identifiers) != len(set(identifiers)) or action not in {'fix', 'reject', 'defer', 'pending'} or not reason.strip():
            raise ReviewError('Supply distinct finding IDs, a decision and its reason')
        with self.access(True) as access:
            for identifier in identifiers:
                self.require_finding(access, identifier)
            return [self.event(access, 'decision', finding=identifier, action=action, reason=reason) for identifier in identifiers]

    def active_batch(self, access=None):
        active = None
        for event in self.rows("events", access):
            if event["kind"] == "batch_open":
                active = event
            if event["kind"] in {"batch_done", "batch_cancel"}:
                active = None
        return active

    def open_batch(self, identifiers):
        if not identifiers or len(set(identifiers)) != len(identifiers):
            raise ReviewError("A fix batch needs distinct finding IDs")
        with self.access(True) as access:
            if self.state(access) != "active" or self.active_batch(access):
                raise ReviewError("Session must be active and have no open fix batch")
            if any(item["status"] in ACTIVE for item in self.rows("rounds", access)):
                raise ReviewError("Finish the review before changing source files")
            findings = {item["id"]: item for item in self.findings(access)}
            if any(findings.get(identifier, {}).get("decision") != "fix" for identifier in identifiers):
                raise ReviewError("Every batch finding needs an explicit fix decision")
            from .source import snapshot, git
            source = snapshot(self.manifest['repo'], self.manifest['base'])
            sequence = len(list((self.directory / 'decisions').glob('*.md'))) + 1
            source['snapshot_ref'] = 'refs/agr/' + self.manifest['id'] + '/fixes/' + str(sequence) + '/before'
            git(self.manifest['repo'], 'update-ref', source['snapshot_ref'], source['tree'], '')
            return self.event(access, "batch_open", findings=identifiers, source=source)

    def close_batch(self, summary, validation, source, cancel=False, identifiers=None):
        if not summary.strip() or (not cancel and not validation.strip()):
            raise ReviewError("Supply the change summary and validation result")
        with self.access(True) as access:
            batch = self.active_batch(access)
            if not batch:
                raise ReviewError("No active fix batch")
            selected = batch['findings'] if identifiers is None else identifiers
            if not selected or len(set(selected)) != len(selected) or not set(selected) <= set(batch['findings']):
                raise ReviewError('Completed findings must belong to the open batch')
            before = batch.get('source')
            values = {'source': source}
            if not cancel and before and source.get('tree'):
                from .source import git, diff
                from .documents import atomic_bytes
                source['snapshot_ref'] = 'refs/agr/' + self.manifest['id'] + '/fixes/' + str(batch['sequence']) + '/after'
                git(self.manifest['repo'], 'update-ref', source['snapshot_ref'], source['tree'], '')
                path = self.directory / 'fixes' / ('%04d.diff' % batch['sequence'])
                atomic_bytes(path, diff(self.manifest['repo'], before['tree'], source['tree']))
                values.update(before=before, diff=str(path.relative_to(self.directory)))
            return self.event(access, "batch_cancel" if cancel else "batch_done", batch=batch["sequence"], findings=selected, summary=summary, validation=validation, **values)

    def set_state(self, status, reason):
        if not reason.strip():
            raise ReviewError("A reason is required")
        with self.access(True) as access:
            if any(item["status"] in ACTIVE for item in self.rows("rounds", access)):
                raise ReviewError("Cancel or finish the current round first")
            return self.event(access, "session_state", status=status, reason=reason)

    def export(self):
        with self.access() as access:
            return {
                "session": self.manifest, "status": self.state(access), "rounds": self.rows("rounds", access),
                "findings": self.findings(access), "events": self.rows("events", access),
                "active_batch": self.active_batch(access),
            }
