# Speech2Text Live (experimental)

Speech2Text Live provides local GPU dictation in an editable Copy-preview. The default shortcut is Alt+Super+Space: press once to start and again to finish. Preferences also offer push-to-talk, which finishes when Space is released. Copy is explicit; the extension never automatically types, pastes, or submits. IBus direct delivery is not available.

## Requirements and setup

GNOME Shell 46 or 47, Python 3.9 or newer, FFmpeg with PulseAudio-compatible capture, and a locally installed CUDA-enabled whisper.cpp server and Small model are required. The experimental extension is packaged with `sh scripts/package-live-preview.sh`; it uses a separate UUID and does not alter the original extension or its backend.

The example user unit in `systemd/speech2text-live-preview.service` assumes a checkout at `~/dev/gnome-speech2text-live`, the model at `~/.cache/whisper.cpp/ggml-small.bin`, and `~/.local/bin/whisper-server`. Review and adapt the unit for your system before installing it. The service owns a dedicated loopback server on port 18080; it never starts a microphone. Its supervisor validates startup GPU facts and writes a private, process-bound runtime configuration. If evidence or a prior request's completion is ambiguous, dictation fails closed rather than falling back to CPU.

From the checkout, with the prerequisites already installed, package and install the isolated extension and configure its private schema (these commands do not install the original extension):

```sh
sh scripts/package-live-preview.sh
gnome-extensions install dist/speech2text-live-study@local.invalid.zip
schema_dir="$HOME/.local/share/gnome-shell/extensions/speech2text-live-study@local.invalid/schemas"
schema=org.gnome.shell.extensions.speech2text-live-study
gsettings --schemadir "$schema_dir" set "$schema" mode preview
gsettings --schemadir "$schema_dir" set "$schema" project-root "$PWD"
gsettings --schemadir "$schema_dir" set "$schema" runner-config "$PWD/runtime/live-preview/preview-runner.json"
```

After reviewing the example unit and its paths, install and enable the dedicated service with the provided scoped script, then start it and enable the extension:

```sh
sh scripts/install-everyday-lifecycle.sh
systemctl --user start speech2text-live-preview.service
gnome-extensions enable speech2text-live-study@local.invalid
```

The script refuses to overwrite an unowned unit. The backend must be ready with valid GPU evidence before recording. Enabling the extension does not start recording. Changes to extension JavaScript may require a manual GNOME logout/login. Keep the checkout and model in place while using the example unit.

## Safety and limitations

Review the preview and use Copy yourself. A maximum recording timeout cancels rather than submitting. No silence suppression is provided. Physical keyboard routing, GPU behavior, and editor compatibility vary across systems; test them explicitly. The direct IBus bridge and automatic application insertion are not supported. No browser, cloud, or CPU fallback is configured.

## Rollback

Close the preview, disable the study extension, and stop and disable only its dedicated user service. Preserve ambiguous in-flight fences until both the corresponding client and owned server epoch are known to have ended. Restore any previous extension's enabled state if needed; do not alter unrelated backend services.
