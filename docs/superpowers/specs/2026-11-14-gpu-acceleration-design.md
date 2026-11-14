# GPU acceleration (NVIDIA): design

Date: 2026-11-14. Status: revision 3, after two reviews; draft for the owner's
review.

## Why

Natural Reader is a community project: people run it on their own machines.
Many of them have an NVIDIA card. Today only the chat model uses it, because
Ollama (outside our container) drives the GPU itself. Everything we run
ourselves stays on the CPU:

| Part | Library | Today |
|---|---|---|
| Read-aloud (Kokoro) | ONNX Runtime | CPU. `server/model.py` already tries CUDA first, but only the CPU build of ONNX Runtime is installed, and the container gets no GPU |
| PDF import (Docling, the Docling build only) | PyTorch | CPU in the image, which installs CPU-only torch on purpose (`Containerfile`). Docling picks its own device; our code never sets one |
| Speech-to-text (Whisper) | CTranslate2 via faster-whisper | CPU, hard-coded: `device="cpu", compute_type="int8"` in `server/speech/local_whisper.py` |
| Speech-to-text, the `transformers-whisper` engine | PyTorch via Transformers | CPU, hard-coded: `device="cpu"` in `server/speech/hf_whisper.py` |

A deployer with an RTX card should be able to say so in one line of `.env`,
rebuild, and have **read-aloud and PDF import** run on it. Speech-to-text stays
on the CPU in this version (see "The CUDA decision").

## Goals

- **One setting picks the hardware.** `ACCELERATOR=cpu` (the default) or
  `ACCELERATOR=nvidia`, in `.env`.
- **Nobody downloads what they don't have.** The CPU image stays as it is. The
  NVIDIA libraries are installed only when `ACCELERATOR=nvidia`. Nothing is
  compiled on the deployer's machine. A bare-metal CPU install stops pulling
  PyTorch's CUDA build too (see "Bare metal").
- **It never stops the app from starting, and never leaves a part broken.**
  Whatever goes wrong with the GPU, at load or later (no card visible, wrong
  driver, out of memory), the affected part runs on the CPU and says why.
- **The deployer can see what landed where.** A line per part in Admin →
  Deployment, and the same lines in the backend log.
- **Each part can be kept on the CPU** while the other uses the card.
- **A part goes on the GPU only if it's faster there,** measured on a real card.

## Non-goals

- **Speech-to-text on the GPU, in this version.** CTranslate2 has no CUDA 13
  build, and the other two need CUDA 13 (see "The CUDA decision"). Both
  speech engines (`local_whisper.py`, `hf_whisper.py`) stay as they are. The
  existing separate-server route is documented instead.
- **AMD (ROCm), Intel GPUs and NPUs, Apple (Metal).** Not looked into yet. The
  setting is shaped so they can be added as new values later. The existing
  bare-metal OpenVINO path in `server/model.py` stays as it is.
- **GTX 10-series and older** (Pascal and before). Dropped by the owner. The
  floor is the **RTX 20-series** (Turing, compute capability 7.5), which the
  CUDA 13 builds support.
- **Windows** (`startup.ps1`, bare metal). Stays CPU in this version. My call.
- **Managing GPU memory.** We don't measure, reserve or share out memory, and
  never stop, limit or move Ollama. That is the deployer's choice: Ollama
  spills to RAM by itself, and can point at another machine.
- **Separate GPU services.** Everything stays in the one backend process (see
  "One process").
- **TensorRT,** or anything else that builds per-card engines on first run.
- **Choosing among several GPUs.** The first visible card is used.
  `CUDA_VISIBLE_DEVICES` stays the escape hatch and gets one line in the docs.

## Anti-goals (failure even if it works)

- The CPU image, or a CPU deployer's `start`, gets bigger, slower or different.
- The app refuses to start, or a part stops working, because of the GPU. That
  includes a GPU failure long after a model loaded.
- The status says "GPU" when the part is actually running on the CPU.
- A part on the GPU that is slower than it was on the CPU.
- A deployer has to compile something, or hand-install CUDA inside the
  container.

## Decisions

**The owner's:**

1. NVIDIA only, RTX 20-series and newer.
2. Chosen in `.env`; the build installs only what the choice needs; CPU is the
   default.
3. On any GPU failure, fall back to the CPU and say so.
4. GPU memory is the deployer's business, not ours.
5. Keep everything in one deployment (one backend process), and keep ONNX
   Runtime and CTranslate2.
6. The status goes in Admin → Deployment, plus the log.
7. Tested on a real RTX machine before it ships. There is no working GPU on
   the development machine, so nothing in this design can be run here.
8. **Speech-to-text stays on the CPU in the first version** (2026-11-14,
   choosing among the options in "The CUDA decision").

**My calls (the owner can overturn any):**

- **One switch for both parts,** with a per-part override.
- **Setting names:** `ACCELERATOR` for the hardware; `TTS_DEVICE` and
  `PDF_IMPORT_DEVICE` for the per-part override. Not `DOCLING_DEVICE`, which is
  Docling's own (see "The settings"). `SPEECH_DEVICE` is kept for the follow-up
  that puts speech on the GPU, and is not read in this version.
- **The container comes first,** because `./startup.sh start` is the documented
  way to deploy. A bare-metal Linux install gets the same packages from
  `./startup.sh init`.
- **More than one worker gets a warning,** not a refusal.
- **Windows stays out** of this version.

## How it works

### The settings

```bash
# .env
ACCELERATOR=nvidia          # cpu (default) | nvidia
# TTS_DEVICE=cpu            # keep read-aloud on the CPU
# PDF_IMPORT_DEVICE=cpu     # keep PDF import on the CPU
```

- `ACCELERATOR` decides two things: which packages the image installs, and the
  device each part *tries* first.
- A per-part setting accepts `cpu` or `gpu`. Unset means "follow
  `ACCELERATOR`". `gpu` under `ACCELERATOR=cpu` does nothing (no GPU libraries
  are installed), and the status says so.
- An unknown value is refused with the list of valid values. `startup.sh`
  refuses a bad `ACCELERATOR`; the backend logs and ignores a bad per-part
  value.
- **Why not `DOCLING_DEVICE`:** Docling reads that variable itself, and accepts
  only `auto`, `cpu`, `cuda`, `mps` or `xpu`. Compose passes `.env` into the
  container, so `DOCLING_DEVICE=gpu` makes Docling raise a `ValidationError`
  when its pipeline options are imported, and every PDF import fails (checked
  in the venv, 2026-11-14). Setting the device in our code is not enough to
  prevent that: Docling builds a default options object of its own while its
  module loads, before our code can pass anything (also checked: a valid
  value is overridden by ours, an invalid one still raises). So the backend
  **removes `DOCLING_DEVICE` from its own environment before Docling is first
  imported**, logs once that it was ignored and which setting to use instead,
  and then sets the device explicitly. A deployer who already sets
  `DOCLING_DEVICE` themselves is therefore overridden; the docs say so.
- **The image remembers its build.** The value of `ACCELERATOR` the image was
  built with is recorded in the image, so the backend knows whether the GPU
  libraries are there. It is what lets the status say "this image has no GPU
  libraries" to a per-part `gpu` setting, and it catches an image built by
  hand with a different build argument. It can't catch an edited `.env`: the
  container's environment is fixed when the container is created, so a
  running backend never sees the edit.
- **Which command applies a change.** `./startup.sh start` builds the image
  every time and recreates the backend when the image changed, so it applies
  a new `ACCELERATOR`. `TTS_DEVICE` and `PDF_IMPORT_DEVICE` don't change the
  image, so `start` leaves the old container (and its old settings) running:
  those need `./startup.sh rebuild`. The docs say so.

### What the image installs

The `Containerfile` already takes build arguments (`WITH_DOCLING`, `APP_UID`).
Add `ACCELERATOR`:

- **`cpu`:** exactly today's install. No change in packages, layers or size.
- **`nvidia`:**
  - `onnxruntime-gpu` in place of `onnxruntime`, at the same version (1.30.0).
    Both claim the same import name, so the CPU package must be removed, not
    just shadowed. `kokoro-onnx` depends on `onnxruntime` and pulls the CPU
    one back if installed after.
  - With Docling, PyTorch's CUDA 13 build instead of the CPU-only one.
  - The CUDA 13 libraries these need, from pip (NVIDIA's `nvidia-*` wheels
    that `onnxruntime-gpu` and `torch` already depend on). No `nvidia/cuda`
    base image.
- **Pins:** one `constraints.txt` still covers both images in this version.
  `onnxruntime-gpu` is its own package name and pins at the same 1.30.0 as
  `onnxruntime`. A single `torch==X` pin matches both the CPU build (`X+cpu`)
  and the CUDA build (`X`). PyTorch isn't pinned today (it arrives with
  Docling); it gets a pin now. A second constraints file is needed only if a
  later version makes one package differ between the images.
  `server/tests/test_requirements_pinned.py` enforces floors, ceilings and
  pins for the lists it reads; a new list (for example
  `requirements-nvidia.txt`) must be added to its `LISTS`.

`startup.sh` reads `ACCELERATOR` from `.env` and passes it as a build argument,
the way `docling_build_flag` passes `WITH_DOCLING` today.

### What the host needs, and how the container gets the card

The deployer installs, once, outside our reach:

- the NVIDIA driver, new enough for CUDA 13, and
- the NVIDIA Container Toolkit.

On podman, the toolkit exposes the card through CDI: the device name is
`nvidia.com/gpu=all` and the spec file is generated by `nvidia-ctk cdi generate`.
On docker, compose's GPU device reservation does it.

Following the existing overlay pattern (`compose_files` in `startup.sh` adds
`compose.google.yml` and friends only when their setting is on), add a GPU
overlay, used only when `ACCELERATOR=nvidia`, with the engine-specific
device line.

**`startup.sh`** checks the prerequisite before building when
`ACCELERATOR=nvidia`. When it's missing, it says exactly what is missing and
the command that fixes it (for example, "no CDI spec for nvidia.com/gpu: run
`sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml`"), then carries on
in CPU mode. Carrying on means **leaving the GPU overlay out**: a container that
asks for a device the host can't provide is never created at all.

**Reboots and driver updates.** After a reboot the engine restarts the existing
container itself, without `startup.sh`, so the check above doesn't run. If the
CDI spec went stale in the meantime (a driver update is the likely cause; not
testable here), the backend container may not start at all. The docs say to
regenerate the CDI spec after every driver update. The RTX walk tests a reboot
(see "Testing").

### Bare metal

`./startup.sh init` installs the backend's Python packages
(`requirements.txt` and `requirements-docling.txt`) with pip's default index.
On Linux, PyPI's default PyTorch is the CUDA build: several GB of NVIDIA
libraries for every bare-metal install, CPU or not. Docling, which picks its
own device, may also be using a card on such installs today. That is inferred
from the install command, not seen: this machine's venv has `2.14.1+cpu`.

- With `ACCELERATOR=cpu`, `init` installs PyTorch from the CPU index, as the
  image does. Docling is set to the CPU explicitly.
- With `ACCELERATOR=nvidia`, `init` installs the NVIDIA set and swaps
  `onnxruntime` for `onnxruntime-gpu`, as the image does. The host needs only
  the driver; no container toolkit.
- The release notes tell bare-metal deployers with a card to set
  `ACCELERATOR=nvidia`, or Docling moves to the CPU.

### Each part at runtime

- **Read-aloud (`server/model.py`).** Already lists CUDA first when the GPU
  build of ONNX Runtime is present. Add: honour `TTS_DEVICE=cpu`. Record the
  provider the session *actually* got (`session.get_providers()`), not the
  one asked for; ONNX Runtime quietly falls back to the CPU when CUDA fails to
  load. Keep the model loading once per worker at import, as today.
- **PDF import (`server/services/docling_convert.py`).** Set Docling's device
  explicitly in `_build_converter`, from `PDF_IMPORT_DEVICE` and
  `ACCELERATOR`, through Docling's accelerator options, so the status can
  report what was chosen. Conversion already runs in a thread of the backend
  process (`asyncio.to_thread`), so it shares that process's GPU setup.
- **Speech-to-text.** Unchanged in this version (see "The CUDA decision").

### Fallback rules

| What happens | Result |
|---|---|
| `ACCELERATOR=nvidia`, but no card visible in the container (toolkit missing, `--device` absent) | Both parts on the CPU. Status: "CPU (no NVIDIA GPU visible to the server)" |
| Driver too old for the CUDA libraries | Both parts on the CPU. Status names the reason the runtime gave |
| A part kept on the CPU by its setting | That part on the CPU. Status: "CPU (kept on the CPU by TTS_DEVICE)" |
| Out of GPU memory when a model loads | That part on the CPU, the other unaffected. Status: "CPU (the GPU was out of memory)" |
| A CUDA failure **during a request**, such as out of memory because Ollama filled the card after our model loaded | That part moves to the CPU for the rest of the worker's life (its model reloads on the CPU), and the failed request is retried once on the CPU: the sentence for read-aloud, the whole import for PDF import. Status: "CPU (the GPU failed while running: out of memory)". It returns to the GPU only on a restart |

Every fallback is logged once per worker at warning level, with the reason.

### Where the deployer sees it

Admin → Deployment already has "Server settings (read-only)", fed by
`readonly_rows()` in `server/services/deployment_info.py`. Add:

- **Read-aloud:** `GPU (NVIDIA GeForce RTX 3060)`, or `CPU (reason)`
- **PDF import:** the same, or `not loaded yet` until the first import, or
  `not installed (this build has no Docling)`
- **Hardware:** `NVIDIA (this image has the GPU libraries)`, `CPU`, or
  `ACCELERATOR is nvidia, but this image was built without the GPU libraries:
  run ./startup.sh rebuild`

There is no speech-to-text row in this version: it would be a fixed sentence,
not something the runtime reported, and wrong for a deployment whose speech
runs on Google or another server. The docs say speech stays on the CPU.

Each row's value comes from what the runtime reported, never from the
settings. The env line under each row names the setting that decided it, as
the other rows do. The same lines go to the backend log when each part loads,
and again when a part moves to the CPU.

### Several workers

`WORKERS` defaults to 1. With more, each worker loads its own Kokoro and
Docling models, with a GPU setup cost of its own (very roughly 0.3–0.5 GB
each, not measured here). A later worker can run out of memory and fall back
when an earlier one didn't. So:

- `startup.sh` warns when `ACCELERATOR=nvidia` and `WORKERS` > 1, saying the
  models are loaded once per worker.
- The Deployment rows describe the worker that answered the request, and say
  so in the row's help text.

### One process, and the separate route that already exists

Everything stays in the backend process. Splitting the parts into services
would add a GPU setup cost per service, more containers to debug, and no
useful latency gain: a local hop is about a millisecond against tens to
hundreds of milliseconds of inference.

For a deployer who wants speech-to-text on a GPU now, a route already exists
and needs only documenting next to this feature: `SPEECH_ENGINE=openai` with
`SPEECH_URL` pointing at any server that speaks OpenAI's transcription API,
run on the card in its own process. The catch, which the docs must state:
choosing a Sinhala or Tamil model per person only works with the built-in
engine.

## The CUDA decision (settled 2026-11-14)

All the libraries load into one process, so their CUDA versions must agree.
They don't:

| Library | Version | CUDA major | Checked |
|---|---|---|---|
| `onnxruntime-gpu` | 1.30.0 (our pin) | 13 | PyPI metadata: depends on `nvidia-cuda-runtime~=13.0` and `nvidia-cudnn-cu13` |
| `torch` | 2.15.0 (newest) | 13 | PyPI metadata: depends on `nvidia-cudnn-cu13` |
| `ctranslate2` | 4.8.4 (our pin) and 4.9.0 (newest, 2026-11-10) | 12 | Both binaries load `libcublas.so.12` |

The owner chose: **ONNX Runtime and PyTorch on CUDA 13, speech-to-text on the
CPU.** The alternatives were all three on CUDA 12 (the last CUDA 12 releases,
ONNX Runtime 1.26 and PyTorch 2.14, both at the end of that line), or mixing
both majors in one process (untestable here; cuDNN, whose CUDA 12 and 13
builds share the file name `libcudnn.so.9`, is the likely collision).

**How speech joins later**, in a follow-up with its own design:

- CTranslate2 publishes a CUDA 13 build, or
- the RTX walk shows CTranslate2's CUDA 12 libraries working next to the CUDA
  13 ones in one process (a walk step below).

The follow-up then needs what this version leaves out: `SPEECH_DEVICE`, a
status row per loaded speech model (up to `SPEECH_MODELS_LOADED`, default 3,
each falling back on its own), `compute_type="int8_float16"` on the GPU (it
keeps the int8 weights of our converted Sinhala and Tamil models), and
probably `LD_LIBRARY_PATH` pointed at the pip-installed NVIDIA libraries,
which CTranslate2 is said to need (unverified).

**Still for the builder:** confirm the CUDA 13 builds of ONNX Runtime and
PyTorch cover the newest cards (RTX 50, Blackwell), and that ONNX Runtime finds
the pip-installed CUDA libraries on its own (it has a preload helper). A card
newer than a library's build is the classic "no kernel image is available"
failure.

## Testing and proof

CI has no GPU, so the automated tests prove the decisions and the real-card
walk proves the hardware.

**Automated (in CI):**

- Device choice per part, from `ACCELERATOR` and the per-part settings,
  including bad values. Use fakes for ONNX Runtime and Docling, as the speech
  tests fake engines today.
- Each row of the fallback table, with the runtime faked to fail the way it
  really fails (no CUDA provider, an out-of-memory error at load, a CUDA error
  during a request), and the status row it produces. The request after a
  mid-request failure runs on the CPU.
- The status rows come from what the runtime reported, never from the
  settings: a test where the setting says GPU and the runtime says CPU.
- The Hardware row when `ACCELERATOR` and the image's recorded build disagree.
- `DOCLING_DEVICE` set to a bad value doesn't break PDF import: it is removed
  from the environment before Docling is imported, and the removal is logged.
  This one needs the real Docling, so it skips where Docling isn't installed.
- `startup.sh` (`server/tests/test_startup_script.py`, stub binaries):
  - `ACCELERATOR` reaches the build as an argument, and the GPU overlay is
    added only for `nvidia`.
  - A typo is refused.
  - A missing toolkit is named, and the build carries on in CPU mode without
    the overlay.
  - `WORKERS` > 1 warns.
  - `init` uses the CPU index for PyTorch unless `nvidia`.
- The CPU image's package list is unchanged by this work (a test over the
  `Containerfile` and requirement lists, like `test_requirements_pinned.py`).
- **A CI job with the NVIDIA package set** builds the NVIDIA image (proving
  the packages install together), then runs the backend suite against the
  same package set. The image ships no test packages, and an existing test
  enforces that, so the suite runs either on the runner with the NVIDIA set
  installed or in a container made from the image with `requirements-dev.txt`
  added for the job. No GPU is needed, and it exercises the real "no card
  visible" fallback with the real libraries. It is several GB, so it runs on
  pushes to `master` and on demand, not on every pull request. My call; the
  builder may adjust.

**On a real RTX machine, before release (owner-provided):**

1. `ACCELERATOR=nvidia`, `./startup.sh start`. Admin → Deployment shows GPU
   for read-aloud, and for PDF import after the first import.
2. Read a page aloud; import a PDF with the Docling build. Each works, and
   `nvidia-smi` shows the backend process using the card.
3. **Speed, per part:** time the same page read aloud (short sentences and
   long ones) and the same PDF imported, with the part on the GPU and with
   its setting at `cpu`. A part that isn't faster on the GPU keeps the CPU as
   its default when `ACCELERATOR=nvidia`, and the docs say so. Read-aloud is
   the one to watch: from memory (not verified), Kokoro under ONNX Runtime's
   CUDA path can lose to the CPU on short sentences.
4. `TTS_DEVICE=cpu` and rebuild: read-aloud shows "CPU (kept on the CPU…)",
   PDF import stays on the GPU.
5. Fill the card after the models loaded (a large Ollama model), then read
   aloud and import: the part that fails moves to the CPU, says so, and the
   request still succeeds.
6. Remove the GPU overlay (or the CDI spec): the app starts, both parts show
   CPU with the reason.
7. Reboot the machine without running `startup.sh`: the stack comes back on
   the GPU. If a driver update is possible on that machine, repeat after it
   and record what happens.
8. Build the image by hand without the build argument (`podman build -f
   Containerfile -t natural-reader-backend .`) and recreate the backend with
   `ACCELERATOR=nvidia` still in `.env`: the Hardware row says the image was
   built without the GPU libraries, and both parts show CPU. Then
   `./startup.sh start` puts it right by itself.
9. **Speech, for the follow-up:** in a scratch venv with the NVIDIA set, load a
   CTranslate2 Whisper model on the GPU in the same process as a CUDA Kokoro
   session, and transcribe a clip. Record whether it works. Nothing ships
   from this step.
10. `ACCELERATOR=cpu` and rebuild: identical to today.

The release notes name the card(s) it was walked on. Anything not walked is
said plainly, as the Gemini adapter's notes did.

## Docs to change

- `docs/DEPLOYMENT.md`: replace "CPU only" with an NVIDIA section covering:
  - prerequisites per engine;
  - the settings, and why not `DOCLING_DEVICE`;
  - the status rows and the fallback table;
  - which command applies a change (`start` for `ACCELERATOR`, `rebuild` for
    the per-part settings);
  - regenerating the CDI spec after a driver update;
  - `WORKERS` and GPU memory;
  - `CUDA_VISIBLE_DEVICES`;
  - speech-to-text staying on the CPU, and the `SPEECH_ENGINE=openai` route.
- `README.md`, "Hardware Acceleration": the container path for NVIDIA. The
  bare-metal ONNX Runtime swap stays for Intel.
- `.env.example`: the three settings, commented, near `WORKERS`.
- `docs/ARCHITECTURE.md`: the device choice and where its status is recorded.
- Release notes: bare-metal deployers with a card set `ACCELERATOR=nvidia`.

## Left to the builder

- The exact pins (PyTorch's new one included) and the `nvidia-*` set.
- How the GPU name is found (for example `nvidia-smi`, which the toolkit
  mounts, or what the runtime reports), and the exact wording of status
  reasons.
- The compose syntax per engine for exposing the card, and how `startup.sh`
  detects the toolkit on each engine.
- How the image records its build (an environment variable or a label).
- How the code reaches the status rows (a small module that each part reports
  into is one shape), and how a part reloads itself on the CPU after a
  mid-request failure. For read-aloud this is more than swapping a variable:
  `server/endpoints.py` imports the Kokoro object by name at start-up
  (`from .model import kokoro`), so a replacement made in `server/model.py`
  never reaches it. The callers need to ask for the current model each time.
  For PDF import, the cached converters (`_converters`) built for the GPU
  have to be dropped.

## Open risks

- **Speed.** Unmeasured. A part may not win on the GPU, which the walk
  settles.
- **Image size.** The NVIDIA variant will be several GB larger. That is
  accepted (opt-in), but the docs should give the real number once built.
- **Reboots after a driver update** may leave the container unable to start.
- **Untestable here.** Every GPU behaviour is unproven until the walk on the
  owner's RTX machine. The newest cards (RTX 50) may need a second machine, or
  a community tester.
- **Docling's own model choices on a GPU** (OCR engine, picture description)
  haven't been looked at. Docling may pick different defaults when CUDA is
  present.
