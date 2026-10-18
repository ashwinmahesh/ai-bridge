# macOS signing and notarization

This is a maintainer workflow. End users do not need an Apple Developer account or signing certificate. A successful release is signed with Developer ID Application, accepted by Apple's notary service, and distributed as a stapled DMG. This does not publish a release or install/start the daemon on the maintainer's computer.

## One-time maintainer setup

Use a Mac with Xcode command-line tools, Apple Developer Program membership, a valid **Developer ID Application** certificate and its private key in the Keychain. Apple Distribution and ad-hoc certificates are not suitable substitutes. Inspect available identities:

```bash
security find-identity -v -p codesigning
```

Create a notarytool credential profile locally, entering your own Apple ID and team ID. Omitting `--password` prompts for an app-specific password in Terminal, so it does not appear in shell history or this repository:

```bash
xcrun notarytool store-credentials ai-bridge-notary \
  --apple-id YOUR_APPLE_ID --team-id YOUR_TEAM_ID
```

Create the app-specific password using your Apple Account's security settings. The profile name is an arbitrary local alias; it is not a password. Do not paste passwords, private keys, `.p12` files, or API credentials into source, issues, or agent messages. App Store Connect API-key authentication is another notarytool option; keep those credentials in local secure storage too.

## Build a signed release

```bash
make build
export CODE_SIGN_IDENTITY='Developer ID Application: YOUR NAME (YOUR_TEAM_ID)'
make signed-build
AI_BRIDGE_TEST_BINARY="$PWD/dist/signed/ai-bridge" python3 tests/test_service.py -v
```

The signed build is separate from ordinary `dist/ai-bridge` and lives in `dist/signed/`. PyInstaller signs the executable **and its embedded Python libraries** using the selected identity, a secure timestamp, and hardened runtime. Do not replace this with a codesign pass over only an already-packed one-file executable. No additional entitlements are required by this service; do not add debugging, JIT, or disabled library-validation entitlements unless a demonstrated dependency needs them.

The build verifies the binary signature, stages only the executable/docs/skill, makes a compressed DMG, and signs it. It excludes credentials, provider state, project transcripts and build environments. Native architecture is used: arm64 and x86_64 need their own appropriate build environments. Passing this step means **signed**, not **notarized**.

## Submit to Apple, staple, and assess

```bash
make notarize NOTARY_PROFILE=ai-bridge-notary
make verify-release
```

`make notarize` uploads the DMG to Apple using the Keychain profile, waits up to ten minutes, and writes `dist/signed/*.notarization.json`. Only an `Accepted` response proceeds to stapling and Gatekeeper assessment. A rejection, timeout, missing identity/profile, or failed assessment stops the workflow; do not describe that artifact as a finished notarized release.

A timeout does not cancel Apple's submission. Check its status before resubmitting:

```bash
xcrun notarytool history --keychain-profile ai-bridge-notary
xcrun notarytool info SUBMISSION_ID --keychain-profile ai-bridge-notary
xcrun notarytool log SUBMISSION_ID --keychain-profile ai-bridge-notary developer-log.json
```

If an existing submission was accepted after the wait timed out, complete the remaining steps manually rather than creating another submission:

```bash
xcrun stapler staple dist/signed/ai-bridge-macos-arm64.dmg
make verify-release
```

Substitute x86_64 when appropriate. The `DMG=/absolute/path/to/file.dmg` Makefile override selects a custom image for notarization/verification. A ticket can be stapled to the DMG; the bare CLI executable or ZIP cannot carry a stapled ticket. Distribute the accepted, stapled DMG unchanged rather than repackaging its contents as an allegedly stapled ZIP. Signing the DMG alone is insufficient: the executable and embedded runtime must be signed too.

Verify a release on another Mac using a browser-downloaded copy, retaining its normal quarantine attribute. Test installation, authentication setup, API messaging, and login startup with that user's own account; avoid relying only on the build Mac's cached Gatekeeper state.

## End-user installation

Open the downloaded DMG, copy `ai-bridge` to a local folder, then run it from Terminal:

```bash
./ai-bridge install-service --start
```

The installer copies it to a stable per-user location, so the DMG can then be ejected. The provider CLIs still need to be installed and signed into separately. Installation details and port conflicts are documented in README.md.

## CI

The normal CI build deliberately produces ad-hoc-signed test artifacts and never accesses release credentials. Use this local Keychain-based workflow for notarized releases. Automated notarized CI would need a separately protected signing keychain and notarization credentials; adding secrets is a maintainer action, not a requirement for users downloading the tool.

References: [Apple Developer ID](https://developer.apple.com/developer-id/), [Apple notarization workflow](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow), [PyInstaller macOS code signing](https://pyinstaller.org/en/stable/feature-notes.html#macos-binary-code-signing).
