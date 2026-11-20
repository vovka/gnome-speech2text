import assert from "node:assert/strict";
import test from "node:test";
import { PreviewOwnership } from "../src/live/previewOwnership.js";

function fixture() {
  const ui = { text: "", selection: [-1, -1], writes: [], selections: [], cancels: 0 };
  const ownership = new PreviewOwnership({
    getText: () => ui.text,
    setText: (text) => {
      ui.text = text;
      ui.writes.push(text);
      ui.selection = [0, 0];
      ownership.changed();
      ui.onSetText?.();
    },
    getSelection: () => [...ui.selection],
    setSelection: (selection) => {
      ui.selection = selection;
      ui.selections.push(selection);
    },
    cancel: () => { ui.cancels++; ui.onCancel?.(); },
    // These capabilities must never be used by preview ownership.
    focus: () => assert.fail("focus"),
    selectAll: () => assert.fail("selectAll"),
    showPreview: () => assert.fail("showPreview"),
    clipboard: () => assert.fail("clipboard"),
    submit: () => assert.fail("submit"),
  });
  const revision = (values = {}) => ownership.revision({
    event: "revision", generation: 1, sequence: 1, samples: 0,
    final: false, text: "preview", ...values,
  });
  return { ui, ownership, revision };
}

test("begin leaves existing UI untouched; inactive and stale generations are ignored", () => {
  const { ui, ownership, revision } = fixture();
  ui.text = "caller text";
  ui.selection = [3, 1];
  revision();
  ownership.begin(2);
  revision();
  revision({ generation: "2" });
  assert.equal(ui.text, "caller text");
  assert.deepEqual(ui.selection, [3, 1]);
  assert.deepEqual(ui.writes, []);
  revision({ generation: 2 });
  ownership.begin(3);
  revision({ generation: 2, sequence: 2 });
  assert.deepEqual(ui.writes, ["preview"]);
  assert.equal(ui.cancels, 0);
});

test("complete partial revisions replace text; sequence increases and samples never decrease", () => {
  const { ui, ownership, revision } = fixture();
  ownership.begin(1);
  revision({ text: "first", samples: 10 });
  revision({ text: "duplicate", samples: 11 });
  revision({ sequence: 2, samples: 9 });
  revision({ sequence: 2, samples: 10, text: "replacement" });
  revision({ sequence: 1, samples: 20 });
  revision({ sequence: 3, samples: 20, final: true, text: "final" });
  revision({ sequence: 4, samples: 30, text: "too late" });
  assert.deepEqual(ui.writes, ["first", "replacement", "final"]);
  assert.equal(ui.cancels, 0);
  ownership.beforeEdit();
  ownership.revoke();
  assert.equal(ui.cancels, 1);
  revision({ sequence: 5 });
  assert.equal(ui.text, "final");
});

for (const [selection, expected] of [
  [[-1, -1], [-1, -1]], [[4, 1], [3, 1]], [[1, 4], [1, 3]],
  [[2, 2], [2, 2]], [[-2, 20], [0, 3]], [[-1, 2], [-1, 2]],
]) {
  test(`Unicode codepoint selection is preserved/clamped: ${selection}`, () => {
    const { ui, ownership, revision } = fixture();
    ui.selection = selection;
    ownership.begin(1);
    revision({ text: "A😀𐐀" });
    assert.deepEqual(ui.selection, expected);
    assert.equal(ui.cancels, 0);
  });
}

for (const edit of ["insert", "delete"]) {
  test(`beforeEdit revokes before user ${edit}, even when text is unchanged`, () => {
    const { ui, ownership, revision } = fixture();
    ownership.begin(1);
    revision();
    ownership.beforeEdit();
    assert.equal(ui.cancels, 1);
    ui.text = edit === "insert" ? "preview!" : "pre";
    ownership.changed();
    ownership.beforeEdit();
    ownership.revoke();
    revision({ sequence: 2, text: "overwrite" });
    assert.equal(ui.text, edit === "insert" ? "preview!" : "pre");
    assert.equal(ui.cancels, 1);
  });
}

for (const signal of [true, false]) {
  test(`unexpected edits revoke ownership ${signal ? "on changed" : "before next revision"}`, () => {
    const { ui, ownership, revision } = fixture();
    ownership.begin(1);
    revision();
    ui.text = "user edit";
    if (signal) ownership.changed();
    revision({ sequence: 2 });
    assert.equal(ui.text, "user edit");
    assert.equal(ui.cancels, 1);
    assert.deepEqual(ui.writes, ["preview"]);
  });
}

for (const signal of [true, false]) {
  test(`reentrant setText edit is not mistaken for an owned change (signal=${signal})`, () => {
    const { ui, ownership, revision } = fixture();
    ownership.begin(1);
    ui.onSetText = () => {
      ui.text = "reentrant user edit";
      ui.selection = [1, 1];
      if (signal) ownership.changed();
    };
    revision();
    revision({ sequence: 2 });
    assert.equal(ui.text, "reentrant user edit");
    assert.deepEqual(ui.selection, [1, 1]);
    assert.deepEqual(ui.selections, []);
    assert.equal(ui.cancels, 1);
  });
}

test("matching changed signals do not revoke; cancel is safe against reentrant revocation", () => {
  const { ui, ownership, revision } = fixture();
  ownership.revoke();
  ownership.changed();
  ownership.begin(1);
  revision();
  ownership.changed();
  assert.equal(ui.cancels, 0);
  ui.onCancel = () => { ownership.revoke(); ownership.beforeEdit(); };
  ownership.revoke();
  assert.equal(ui.cancels, 1);
  ownership.begin(2);
  revision({ generation: 2 });
  assert.equal(ui.writes.length, 2);
});

const malformed = [
  null, undefined, false, [], "revision",
  ...["event", "generation", "sequence", "samples", "final", "text"].map((key) => ({ [key]: undefined })),
  { event: "partial" }, { generation: "1" }, { generation: 0 }, { generation: -1 },
  { generation: 1.5 }, { generation: Number.MAX_SAFE_INTEGER + 1 },
  { sequence: 0 }, { sequence: -1 }, { sequence: 1.5 }, { sequence: "1" },
  { sequence: Infinity }, { sequence: Number.MAX_SAFE_INTEGER + 1 },
  { samples: -1 }, { samples: 0.5 }, { samples: "0" }, { samples: NaN },
  { samples: Number.MAX_SAFE_INTEGER + 1 }, { final: 1 }, { final: "false" },
  { text: null }, { text: 42 },
];
for (const [index, invalid] of malformed.entries()) {
  test(`malformed revision ${index} is ignored without consuming sequence or sealing`, () => {
    const { ui, ownership, revision } = fixture();
    ownership.begin(1);
    if (invalid && typeof invalid === "object" && !Array.isArray(invalid)) revision(invalid);
    else ownership.revision(invalid);
    assert.deepEqual(ui.writes, []);
    assert.equal(ui.cancels, 0);
    revision({ text: "valid" });
    assert.deepEqual(ui.writes, ["valid"]);
  });
}

test("empty final text is valid and has no automatic clipboard, submit, or focus effects", () => {
  const { ui, ownership, revision } = fixture();
  ownership.begin(Number.MAX_SAFE_INTEGER);
  revision({ generation: Number.MAX_SAFE_INTEGER, sequence: Number.MAX_SAFE_INTEGER,
    samples: Number.MAX_SAFE_INTEGER, final: true, text: "" });
  assert.deepEqual(ui.writes, [""]);
  assert.deepEqual(ui.selection, [-1, -1]);
  assert.equal(ui.cancels, 0);
});
