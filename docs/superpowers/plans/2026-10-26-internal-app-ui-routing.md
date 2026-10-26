# Internal App UI Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide internal browser hostnames for Unpackerr and Agentgateway.

**Architecture:** HTTPRoutes attach to envoy-internal and are discovered by existing k8s-gateway DNS. Agentgateway receives a separate admin Service and non-loopback admin bind; its existing API route remains unchanged.

**Tech Stack:** Flux, Gateway API, app-template, Agentgateway v1.6.0.

**Spec:** `docs/superpowers/specs/2026-10-26-internal-app-ui-routing-design.md`

## Global Constraints

- Use `${SECRET_DOMAIN}`, `envoy-internal` in namespace `network`, and existing namespace policies.
- No public DNS, secret edits, push, apply, or reconciliation.
- Preserve unrelated work and existing API routing.

## Review Focus

- UI supporting assets/API calls must route correctly, not just HTML.
- Agentgateway admin bind must be externally reachable through the new Service.
- Internal DNS discovery must observe the route hostnames without public exposure.
- New Service must select the managed gateway pods, not the controller.
- Existing `llm` API route and Unpackerr password remain unchanged.

### Task 1: Internal UI routes

**Files:** Unpackerr `app/helmrelease.yaml` or new `app/httproute.yaml` plus `app/kustomization.yaml`; Agentgateway `app/gateway.yaml`, new UI Service/HTTPRoute and `app/kustomization.yaml`.

- [x] Inspect UI implementation/served requests and verify required path prefixes, selector, and supported ADMIN_ADDR configuration.
- [x] Confirm current manifests lack the desired hostnames/admin listener Service.
- [x] Add Unpackerr internal route to port 5656; add Agentgateway admin bind, Service port 15000, and internal UI route with necessary supporting paths.
- [x] Run focused parsing/rendering and inspect paths, selector, listener setting, and unchanged API/password configuration.
- [x] Parent reviews final diff and runs `bash .agents/skills/pr-review/scripts/validate-pr.sh` once. Report deployment limitations explicitly.

## Verification outcome

`mise exec -- bash .agents/skills/pr-review/scripts/validate-pr.sh` passed: all Flux renders, shellcheck, secret guardrails, and 24 regression tests. Parent inspected all changed manifests. Agentgateway's UI route allows GETs for `/`, `/ui`, `/api`, and `/config_dump`, plus query-only POSTs for `/api/logs` and `/api/cel`; administration writes are not routed. Source inspection confirmed the supporting data paths. No deployment occurred, so live DNS and full browser operation through these new hostnames remain unverified.
