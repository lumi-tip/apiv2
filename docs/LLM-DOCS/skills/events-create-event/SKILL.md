---
name: events-create-event
description: Use when creating an academy event through the API with optional meeting auto-creation (Daily or LiveKit) and event chat toggle; do NOT use for listing events, joining events, or suspending/deleting existing events.
---

# Skill: Create an Event with Daily or LiveKit Meeting and Chat Toggle

## When to Use

Use this skill when the user asks to create a new academy event via API and may want to auto-create a meeting room (`create_meet`) using either Daily or LiveKit. Use it also when the user needs to define whether Daily in-call chat is enabled (`enable_chat`). Do **not** use this skill for read-only requests (list/get events), join/checkin flows, or lifecycle actions like suspend/delete.

## Concepts

- **Academy event**: Event created under an academy context; request must include academy header and proper capability.
- **Meeting provider**: `meeting_provider` can be `daily` or `livekit`. If omitted, backend uses academy default provider; if academy default is absent, it falls back to global default.
- **Meeting auto-creation**: `create_meet=true` asks backend to create the room and save `live_stream_url`.
- **Chat toggle**: `enable_chat` controls Daily embedded chat visibility for the event (LiveKit flow is unaffected by this flag).

## Workflow

1. **Validate permissions and context first.** Ensure the caller has `Authorization` plus `Academy: <academy_id>` header and capability `crud_event`. If either is missing, stop and ask for correct credentials/context before retrying.
2. **Build the event payload.** Include base event fields (`title`, `banner`, `capacity`, `starting_at`, `ending_at`, `event_type`, `tags`, `online_event`) and set `enable_chat` to `true` or `false` depending on user intent.
3. **Choose meeting provider strategy.** If user explicitly wants Daily or LiveKit, set `meeting_provider` to `daily` or `livekit`. If provider is not specified, omit `meeting_provider` and let backend resolve academy/global default.
4. **Request room auto-creation only when needed.** For online events where backend must generate meeting URL, set `create_meet: true`. If user already provides a valid `live_stream_url`, keep `create_meet` false/omitted.
5. **Call create endpoint and store identifiers.** Send `POST /v1/events/academy/event`. Save at least `id`, `slug`, `online_event`, `live_stream_url`, `enable_chat`, and `status`.
6. **Verify provider/chat intent persisted.** Confirm returned `live_stream_url` matches expected provider style (Daily domain or LiveKit meet URL) and `enable_chat` is correct. If not, call update endpoint with corrected fields.

## Endpoints

| Action | Method | Path | Required Headers | Required Body Fields | Important Response Fields |
|--------|--------|------|------------------|----------------------|---------------------------|
| Create academy event | POST | `/v1/events/academy/event` | `Authorization`, `Academy: <academy_id>` | `banner`, `capacity`, `starting_at`, `ending_at`; plus practical fields `title`, `event_type`, `tags`, `online_event`; for auto-room add `create_meet`; optional provider override with `meeting_provider` (`daily` or `livekit`); chat control with `enable_chat` | `id`, `slug`, `online_event`, `live_stream_url`, `enable_chat`, `status` |
| Update one academy event (optional fallback) | PUT | `/v1/events/academy/event/{event_id}` | `Authorization`, `Academy: <academy_id>` | At least one updatable field (e.g. `enable_chat`, `live_stream_url`, schedule fields) | Updated event object with `id`, `enable_chat`, and current meeting URL fields |

### Sample Request JSON (POST create event with Daily and chat enabled)

```json
{
  "title": "AI Career Session - March",
  "banner": "https://storage.googleapis.com/academy-assets/events/ai-career-session.png",
  "capacity": 120,
  "starting_at": "2026-03-20T18:00:00Z",
  "ending_at": "2026-03-20T20:00:00Z",
  "event_type": 14,
  "tags": "ai,career,community",
  "online_event": true,
  "enable_chat": true,
  "create_meet": true,
  "meeting_provider": "daily"
}
```

### Sample Response JSON (POST create event with Daily)

```json
{
  "id": 9831,
  "uuid": "2e3b9202-9f57-4c40-a38a-7f7d7a7af1fa",
  "slug": "ai-career-session-march",
  "title": "AI Career Session - March",
  "banner": "https://storage.googleapis.com/academy-assets/events/ai-career-session.png",
  "capacity": 120,
  "starting_at": "2026-03-20T18:00:00Z",
  "ending_at": "2026-03-20T20:00:00Z",
  "online_event": true,
  "live_stream_url": "https://your-subdomain.daily.co/event-9831-room",
  "enable_chat": true,
  "tags": "ai,career,community",
  "status": "DRAFT",
  "academy": 1,
  "event_type": 14
}
```

### Sample Request JSON (POST create event with LiveKit provider)

```json
{
  "title": "Data Engineering AMA",
  "banner": "https://storage.googleapis.com/academy-assets/events/data-engineering-ama.png",
  "capacity": 200,
  "starting_at": "2026-04-03T17:00:00Z",
  "ending_at": "2026-04-03T19:00:00Z",
  "event_type": 14,
  "tags": "data,community,ama",
  "online_event": true,
  "enable_chat": false,
  "create_meet": true,
  "meeting_provider": "livekit"
}
```

### Sample Response JSON (POST create event with LiveKit)

```json
{
  "id": 9884,
  "uuid": "f95dbeb2-c52d-41e0-b825-8b1c5a7c4f6f",
  "slug": "data-engineering-ama",
  "title": "Data Engineering AMA",
  "online_event": true,
  "live_stream_url": "https://meet.4geeks.com/rooms/event-9884",
  "enable_chat": false,
  "status": "DRAFT",
  "academy": 1,
  "event_type": 14
}
```

### Sample Request JSON (PUT toggle chat off)

```json
{
  "enable_chat": false
}
```

### Sample Response JSON (PUT updated event)

```json
{
  "id": 9831,
  "slug": "ai-career-session-march",
  "online_event": true,
  "live_stream_url": "https://your-subdomain.daily.co/event-9831-room",
  "enable_chat": false,
  "status": "DRAFT"
}
```

## Edge Cases

- **Missing academy context or insufficient capability:** You will get auth/permission failure. Ask for `Academy` header and a user with `crud_event`; do not retry unchanged.
- **Online event without URL and without auto-create:** If `online_event=true` and no `live_stream_url` is supplied, request can fail unless `create_meet=true` is used. Retry with one of those two options.
- **Daily room auto-create failure:** API may fail if Daily credentials are not configured for the academy. Ask user to configure academy event settings (`daily_api_key`) and retry.
- **LiveKit room auto-create failure:** API may fail if LiveKit meet URL or credentials are missing/invalid. Ask user to configure academy LiveKit settings and retry.
- **Provider not sent:** Backend uses academy default provider; if academy setting does not exist, global default is used. If output provider is not desired, retry with explicit `meeting_provider`.
- **Event validation errors (tags/event type/academy not found):** Return errors like missing tags, missing event type, or unknown academy. Report the exact API error and request corrected payload values before retrying.

## Checklist

1. Confirm request has `Authorization` and `Academy` headers and valid `crud_event` access.
2. Build POST payload with event core fields and explicit `enable_chat` value.
3. If meeting URL must be auto-generated, send `create_meet=true` and set `meeting_provider` to `daily` or `livekit` (or omit it to use academy/global default).
4. Save and report response fields: `id`, `slug`, `live_stream_url`, `enable_chat`, `status`.
5. If `live_stream_url` provider style or `enable_chat` does not match intent, call `PUT /v1/events/academy/event/{event_id}` with corrected values.
