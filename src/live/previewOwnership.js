/** Owns complete preview replacements until an edit, revocation, or a new begin. */
export class PreviewOwnership {
  constructor({ getText, setText, getSelection, setSelection, cancel = () => {} }) {
    this._getText = getText;
    this._setText = setText;
    this._getSelection = getSelection;
    this._setSelection = setSelection;
    this._cancel = cancel;
    this._owner = null;
  }

  // The caller clears the UI first; beginning ownership never changes it.
  begin(generation) {
    if (!Number.isSafeInteger(generation) || generation <= 0) return;
    this._owner = {
      generation, sequence: 0, samples: 0, final: false, expected: this._getText(),
    };
  }

  revision(revision) {
    if (!this._valid(revision)) return;
    const owner = this._owner;
    if (!owner || revision.generation !== owner.generation || owner.final) return;
    if (revision.sequence <= owner.sequence || revision.samples < owner.samples) return;
    if (this._getText() !== owner.expected) {
      this.revoke();
      return;
    }

    const selection = this._getSelection();
    owner.sequence = revision.sequence;
    owner.samples = revision.samples;
    owner.final = revision.final;
    // Install the expectation before setText, which may synchronously emit changed.
    owner.expected = revision.text;
    this._setText(revision.text);
    if (this._owner !== owner) return;
    this.changed();
    if (this._owner !== owner) return;
    const length = Array.from(revision.text).length;
    this._setSelection(selection.map((position) =>
      position === -1 ? -1 : Math.max(0, Math.min(position, length))
    ));
  }

  beforeEdit() {
    this.revoke();
  }

  changed() {
    if (this._owner && this._getText() !== this._owner.expected) this.revoke();
  }

  revoke() {
    if (!this._owner) return;
    this._owner = null;
    this._cancel();
  }

  _valid(revision) {
    return revision !== null && typeof revision === "object"
      && revision.event === "revision"
      && Number.isSafeInteger(revision.generation) && revision.generation > 0
      && Number.isSafeInteger(revision.sequence) && revision.sequence > 0
      && Number.isSafeInteger(revision.samples) && revision.samples >= 0
      && typeof revision.final === "boolean" && typeof revision.text === "string";
  }
}
