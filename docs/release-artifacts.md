# Release image artifacts

This procedure builds deployable backend and frontend image archives from the exact source revision in the current checkout. It does not publish, deploy, log in to a registry, or modify production resources.

## Build locally or in a trusted CI job

The builder intentionally requires the complete revision and both production Dockerfile paths. This repository does not infer Dockerfile names or public registry coordinates:

```text
scripts/release/build-image-artifacts.sh FULL_SOURCE_REVISION \
  --backend-dockerfile PATH_TO_BACKEND_PRODUCTION_DOCKERFILE \
  --frontend-dockerfile PATH_TO_FRONTEND_PRODUCTION_DOCKERFILE \
  --frontend-api-base-url OPERATOR_SUPPLIED_BROWSER_API_ORIGIN \
  [--backend-context backend] [--frontend-context frontend] \
  [--output-root release-artifacts]
```

`OPERATOR_SUPPLIED_BROWSER_API_ORIGIN` is a required non-secret build input and must be the reviewed browser-visible API origin for the target environment; this repository does not invent it.

`FULL_SOURCE_REVISION` must equal the current checkout's complete `HEAD` revision. The builder fails before building if it does not. The default contexts are the repository's `backend` and `frontend` directories; explicit contexts can be supplied when the reviewed production packaging defines different contexts.

The output is placed under `release-artifacts/FULL_SOURCE_REVISION/` (or the supplied output root) and contains:

- `backend.docker.tar` and `frontend.docker.tar`, Docker image archives exported by `docker save` from the production Dockerfiles. These are Docker archives, not OCI-layout archives;
- `manifest.json`, recording the full source revision, local revision-addressed tags, Dockerfile and context identities, image IDs, build timestamp, CI run identifier when available, and archive SHA-256 values;
- `manifest.json.sha256`, the SHA-256 checksum of the manifest.

Every archive and the manifest has a SHA-256 checksum. The metadata is build-record information only; it does not claim reproducible-build provenance. Base-image digest pinning, signing, SBOM generation, and provenance attestations are separate later hardening processes and are not produced by this builder.

The local tags are revision-addressed (`belib-backend:FULL_SOURCE_REVISION` and `belib-frontend:FULL_SOURCE_REVISION`), and no mutable `latest` tag is created. The builder adds source-revision labels to both images. It has no registry login, credential, endpoint, push, deployment, or production mutation step.

## Future publication boundary

A later publication process may publish only artifacts built from the exact reviewed commit, after review and approval for a trusted release tag or protected manual environment. That process must use versioned and commit-addressed tags, capture the registry-provided immutable `sha256` digest for each image, and make deployment consumers use immutable references of the form:

```text
registry-approved-coordinate/image@sha256:registry-provided-digest
```

Registry coordinates, credential names, approval policy, and the protected environment are intentionally operator-supplied and absent here. Publication must be impossible from both `pull_request` and `pull_request_target` execution paths. Missing registry approval or credentials must block publication rather than weaken the build and validation checks. This document does not authorize or implement publication, deployment, registry login, or push.
