--
-- PostgreSQL database dump
--

\restrict zVCWBORbwBLqBEQRhotKfh2VzeU7AwcbW4U0jFGvA4gvR32XgXxnsIBb63Nzb7T

-- Dumped from database version 16.15
-- Dumped by pg_dump version 16.15

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: balance_snapshots; Type: TABLE; Schema: public; Owner: fatty_app
--

CREATE TABLE public.balance_snapshots (
    id uuid NOT NULL,
    exchange text NOT NULL,
    total_balance numeric NOT NULL,
    available_balance numeric NOT NULL,
    equity numeric NOT NULL,
    margin_coin text NOT NULL,
    captured_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT balance_snapshots_exchange_check CHECK ((exchange = ANY (ARRAY['binance'::text, 'bitget'::text])))
);


ALTER TABLE public.balance_snapshots OWNER TO fatty_app;

--
-- Name: bitget_protection_capabilities; Type: TABLE; Schema: public; Owner: fatty_app
--

CREATE TABLE public.bitget_protection_capabilities (
    exchange text NOT NULL,
    environment text NOT NULL,
    symbol text NOT NULL,
    position_mode text NOT NULL,
    margin_mode text NOT NULL,
    native_state text NOT NULL,
    fallback_allowed boolean DEFAULT false NOT NULL,
    payload_profile text NOT NULL,
    last_verified_at timestamp with time zone,
    last_error text,
    stream_state text NOT NULL,
    last_stream_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT bitget_protection_capabilities_environment_check CHECK ((environment = ANY (ARRAY['DEMO'::text, 'LIVE'::text]))),
    CONSTRAINT bitget_protection_capabilities_exchange_check CHECK ((exchange = ANY (ARRAY['binance'::text, 'bitget'::text]))),
    CONSTRAINT bitget_protection_capabilities_native_state_check CHECK ((native_state = ANY (ARRAY['UNKNOWN'::text, 'VERIFIED'::text, 'UNSUPPORTED'::text, 'FAILED'::text]))),
    CONSTRAINT bitget_protection_capabilities_stream_state_check CHECK ((stream_state = ANY (ARRAY['DISABLED'::text, 'CONNECTING'::text, 'HEALTHY'::text, 'STALE'::text, 'FAILED'::text])))
);


ALTER TABLE public.bitget_protection_capabilities OWNER TO fatty_app;

--
-- Name: canary_entry_reservations; Type: TABLE; Schema: public; Owner: fatty_app
--

CREATE TABLE public.canary_entry_reservations (
    dispatch_id uuid NOT NULL,
    exchange text NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT canary_entry_reservations_exchange_check CHECK ((exchange = ANY (ARRAY['binance'::text, 'bitget'::text])))
);


ALTER TABLE public.canary_entry_reservations OWNER TO fatty_app;

--
-- Name: canonical_signals; Type: TABLE; Schema: public; Owner: fatty_app
--

CREATE TABLE public.canonical_signals (
    id uuid NOT NULL,
    message_id uuid NOT NULL,
    revision character(64) NOT NULL,
    pair_token text NOT NULL,
    direction text NOT NULL,
    entry_price numeric NOT NULL,
    stop_loss numeric NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    take_profits jsonb DEFAULT '[]'::jsonb NOT NULL,
    CONSTRAINT canonical_signals_direction_check CHECK ((direction = ANY (ARRAY['LONG'::text, 'SHORT'::text]))),
    CONSTRAINT canonical_signals_entry_price_check CHECK ((entry_price > (0)::numeric)),
    CONSTRAINT canonical_signals_stop_loss_check CHECK ((stop_loss > (0)::numeric))
);


ALTER TABLE public.canonical_signals OWNER TO fatty_app;

--
-- Name: dispatch_transitions; Type: TABLE; Schema: public; Owner: fatty_app
--

CREATE TABLE public.dispatch_transitions (
    id uuid NOT NULL,
    dispatch_id uuid NOT NULL,
    from_state text NOT NULL,
    to_state text NOT NULL,
    reason text,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


ALTER TABLE public.dispatch_transitions OWNER TO fatty_app;

--
-- Name: dispatches; Type: TABLE; Schema: public; Owner: fatty_app
--

CREATE TABLE public.dispatches (
    id uuid NOT NULL,
    source_type text NOT NULL,
    source_id uuid NOT NULL,
    revision character(64) NOT NULL,
    exchange text NOT NULL,
    state text NOT NULL,
    claimed_by text,
    lease_until timestamp with time zone,
    attempts integer DEFAULT 0 NOT NULL,
    terminal_reason text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT dispatches_attempts_check CHECK ((attempts >= 0)),
    CONSTRAINT dispatches_exchange_check CHECK ((exchange = ANY (ARRAY['binance'::text, 'bitget'::text])))
);


ALTER TABLE public.dispatches OWNER TO fatty_app;

--
-- Name: fallback_protection; Type: TABLE; Schema: public; Owner: fatty_app
--

CREATE TABLE public.fallback_protection (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    exchange text NOT NULL,
    symbol text NOT NULL,
    direction text NOT NULL,
    entry_price numeric NOT NULL,
    stop_loss numeric NOT NULL,
    take_profits jsonb DEFAULT '[]'::jsonb NOT NULL,
    quantity numeric NOT NULL,
    state text DEFAULT 'active'::text NOT NULL,
    close_price numeric,
    close_reason text,
    close_order_id text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    position_key text
);


ALTER TABLE public.fallback_protection OWNER TO fatty_app;

--
-- Name: fills; Type: TABLE; Schema: public; Owner: fatty_app
--

CREATE TABLE public.fills (
    id uuid NOT NULL,
    exchange text NOT NULL,
    client_order_id text NOT NULL,
    provider_fill_id text NOT NULL,
    symbol text NOT NULL,
    price numeric NOT NULL,
    quantity numeric NOT NULL,
    fee numeric DEFAULT 0 NOT NULL,
    fee_ccy text,
    realized_pnl numeric DEFAULT 0 NOT NULL,
    filled_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT fills_exchange_check CHECK ((exchange = ANY (ARRAY['binance'::text, 'bitget'::text]))),
    CONSTRAINT fills_price_check CHECK ((price > (0)::numeric)),
    CONSTRAINT fills_quantity_check CHECK ((quantity > (0)::numeric))
);


ALTER TABLE public.fills OWNER TO fatty_app;

--
-- Name: intent_reconciliation_audit; Type: TABLE; Schema: public; Owner: fatty_app
--

CREATE TABLE public.intent_reconciliation_audit (
    id uuid NOT NULL,
    exchange text NOT NULL,
    client_order_id text NOT NULL,
    prior_state text NOT NULL,
    resolved_state text NOT NULL,
    provider_snapshot jsonb NOT NULL,
    approval_reference text NOT NULL,
    reconciled_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


ALTER TABLE public.intent_reconciliation_audit OWNER TO fatty_app;

--
-- Name: live_order_intents; Type: TABLE; Schema: public; Owner: fatty_app
--

CREATE TABLE public.live_order_intents (
    id uuid NOT NULL,
    exchange text NOT NULL,
    client_order_id text NOT NULL,
    provider_order_id text,
    symbol text NOT NULL,
    side text NOT NULL,
    role text NOT NULL,
    state text NOT NULL,
    requested_qty numeric NOT NULL,
    acknowledged_qty numeric,
    filled_qty numeric DEFAULT 0 NOT NULL,
    requested_price numeric,
    acknowledged_price numeric,
    filled_price numeric,
    leverage numeric,
    margin_mode text,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    fee numeric DEFAULT 0 NOT NULL,
    provider_fill_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    CONSTRAINT live_order_intents_acknowledged_price_check CHECK (((acknowledged_price IS NULL) OR (acknowledged_price > (0)::numeric))),
    CONSTRAINT live_order_intents_acknowledged_qty_check CHECK (((acknowledged_qty IS NULL) OR (acknowledged_qty > (0)::numeric))),
    CONSTRAINT live_order_intents_exchange_check CHECK ((exchange = ANY (ARRAY['binance'::text, 'bitget'::text]))),
    CONSTRAINT live_order_intents_filled_price_check CHECK (((filled_price IS NULL) OR (filled_price > (0)::numeric))),
    CONSTRAINT live_order_intents_filled_qty_check CHECK ((filled_qty >= (0)::numeric)),
    CONSTRAINT live_order_intents_leverage_check CHECK (((leverage IS NULL) OR (leverage > (0)::numeric))),
    CONSTRAINT live_order_intents_margin_mode_check CHECK (((margin_mode IS NULL) OR (margin_mode = ANY (ARRAY['ISOLATED'::text, 'CROSS'::text])))),
    CONSTRAINT live_order_intents_requested_price_check CHECK (((requested_price IS NULL) OR (requested_price > (0)::numeric))),
    CONSTRAINT live_order_intents_requested_qty_check CHECK ((requested_qty > (0)::numeric)),
    CONSTRAINT live_order_intents_role_check CHECK ((role = ANY (ARRAY['ENTRY'::text, 'SL'::text, 'TP'::text, 'CLOSE'::text, 'EMERGENCY_CLOSE'::text]))),
    CONSTRAINT live_order_intents_side_check CHECK ((side = ANY (ARRAY['BUY'::text, 'SELL'::text]))),
    CONSTRAINT live_order_intents_state_check CHECK ((state = ANY (ARRAY['requested'::text, 'acknowledged'::text, 'submitted'::text, 'partially_filled'::text, 'filled'::text, 'cancelled'::text, 'rejected'::text, 'unknown'::text, 'reconciled'::text])))
);


ALTER TABLE public.live_order_intents OWNER TO fatty_app;

--
-- Name: notifications_outbox; Type: TABLE; Schema: public; Owner: fatty_app
--

CREATE TABLE public.notifications_outbox (
    id uuid NOT NULL,
    dedup_key text NOT NULL,
    payload jsonb NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    sent_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    claimed_by text,
    lease_until timestamp with time zone,
    next_attempt_at timestamp with time zone,
    failed_at timestamp with time zone,
    CONSTRAINT notifications_outbox_attempts_check CHECK ((attempts >= 0))
);


ALTER TABLE public.notifications_outbox OWNER TO fatty_app;

--
-- Name: operator_telegram_updates; Type: TABLE; Schema: public; Owner: fatty_app
--

CREATE TABLE public.operator_telegram_updates (
    update_id bigint NOT NULL,
    claimed_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


ALTER TABLE public.operator_telegram_updates OWNER TO fatty_app;

--
-- Name: orders; Type: TABLE; Schema: public; Owner: fatty_app
--

CREATE TABLE public.orders (
    id uuid NOT NULL,
    dispatch_id uuid,
    position_id uuid,
    exchange text NOT NULL,
    client_order_id text NOT NULL,
    venue_order_id text,
    role text NOT NULL,
    state text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT orders_exchange_check CHECK ((exchange = ANY (ARRAY['binance'::text, 'bitget'::text]))),
    CONSTRAINT orders_role_check CHECK ((role = ANY (ARRAY['ENTRY'::text, 'SL'::text, 'TP'::text, 'CLOSE'::text])))
);


ALTER TABLE public.orders OWNER TO fatty_app;

--
-- Name: position_snapshots; Type: TABLE; Schema: public; Owner: fatty_app
--

CREATE TABLE public.position_snapshots (
    id uuid NOT NULL,
    exchange text NOT NULL,
    symbol text NOT NULL,
    side text NOT NULL,
    size numeric NOT NULL,
    entry_price numeric,
    mark_price numeric,
    liquidation_price numeric,
    leverage numeric,
    margin_mode text,
    unrealized_pnl numeric DEFAULT 0 NOT NULL,
    captured_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT position_snapshots_entry_price_check CHECK (((entry_price IS NULL) OR (entry_price > (0)::numeric))),
    CONSTRAINT position_snapshots_exchange_check CHECK ((exchange = ANY (ARRAY['binance'::text, 'bitget'::text]))),
    CONSTRAINT position_snapshots_leverage_check CHECK (((leverage IS NULL) OR (leverage > (0)::numeric))),
    CONSTRAINT position_snapshots_liquidation_price_check CHECK (((liquidation_price IS NULL) OR (liquidation_price > (0)::numeric))),
    CONSTRAINT position_snapshots_margin_mode_check CHECK (((margin_mode IS NULL) OR (margin_mode = ANY (ARRAY['ISOLATED'::text, 'CROSS'::text])))),
    CONSTRAINT position_snapshots_mark_price_check CHECK (((mark_price IS NULL) OR (mark_price > (0)::numeric))),
    CONSTRAINT position_snapshots_side_check CHECK ((side = ANY (ARRAY['LONG'::text, 'SHORT'::text])))
);


ALTER TABLE public.position_snapshots OWNER TO fatty_app;

--
-- Name: positions; Type: TABLE; Schema: public; Owner: fatty_app
--

CREATE TABLE public.positions (
    id uuid NOT NULL,
    exchange text NOT NULL,
    symbol text NOT NULL,
    direction text NOT NULL,
    quantity numeric NOT NULL,
    protection_state text NOT NULL,
    opened_at timestamp with time zone DEFAULT now() NOT NULL,
    closed_at timestamp with time zone,
    CONSTRAINT positions_direction_check CHECK ((direction = ANY (ARRAY['LONG'::text, 'SHORT'::text]))),
    CONSTRAINT positions_exchange_check CHECK ((exchange = ANY (ARRAY['binance'::text, 'bitget'::text]))),
    CONSTRAINT positions_protection_state_check CHECK ((protection_state = ANY (ARRAY['PENDING'::text, 'VENUE_PROTECTED'::text, 'BOT_FALLBACK'::text, 'DEGRADED'::text, 'FAILED'::text]))),
    CONSTRAINT positions_quantity_check CHECK ((quantity > (0)::numeric))
);


ALTER TABLE public.positions OWNER TO fatty_app;

--
-- Name: protection_states; Type: TABLE; Schema: public; Owner: fatty_app
--

CREATE TABLE public.protection_states (
    id uuid NOT NULL,
    position_id uuid,
    order_ref text,
    sl_order_id text,
    tp_order_id text,
    state text NOT NULL,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


ALTER TABLE public.protection_states OWNER TO fatty_app;

--
-- Name: provider_reconciliation_events; Type: TABLE; Schema: public; Owner: fatty_app
--

CREATE TABLE public.provider_reconciliation_events (
    id uuid NOT NULL,
    exchange text NOT NULL,
    provider_order_id text,
    provider_fill_id text NOT NULL,
    client_order_id text NOT NULL,
    symbol text NOT NULL,
    side text NOT NULL,
    source text NOT NULL,
    quantity numeric NOT NULL,
    price numeric NOT NULL,
    fee numeric DEFAULT 0 NOT NULL,
    realized_pnl numeric DEFAULT 0 NOT NULL,
    state text NOT NULL,
    observed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT provider_reconciliation_events_exchange_check CHECK ((exchange = ANY (ARRAY['binance'::text, 'bitget'::text]))),
    CONSTRAINT provider_reconciliation_events_fee_check CHECK ((fee >= (0)::numeric)),
    CONSTRAINT provider_reconciliation_events_price_check CHECK ((price > (0)::numeric)),
    CONSTRAINT provider_reconciliation_events_quantity_check CHECK ((quantity > (0)::numeric)),
    CONSTRAINT provider_reconciliation_events_side_check CHECK ((side = ANY (ARRAY['BUY'::text, 'SELL'::text]))),
    CONSTRAINT provider_reconciliation_events_source_check CHECK ((source = ANY (ARRAY['SYSTEM_LIQUIDATION'::text, 'BOT_FALLBACK_CLOSE'::text, 'NATIVE_SL'::text, 'PROVIDER_EXIT'::text]))),
    CONSTRAINT provider_reconciliation_events_state_check CHECK ((state = ANY (ARRAY['reconciled'::text, 'unknown'::text])))
);


ALTER TABLE public.provider_reconciliation_events OWNER TO fatty_app;

--
-- Name: reconciliation_state; Type: TABLE; Schema: public; Owner: fatty_app
--

CREATE TABLE public.reconciliation_state (
    scope text NOT NULL,
    last_run_at timestamp with time zone,
    last_success_at timestamp with time zone,
    mismatch_count integer DEFAULT 0 NOT NULL,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT reconciliation_state_mismatch_count_check CHECK ((mismatch_count >= 0))
);


ALTER TABLE public.reconciliation_state OWNER TO fatty_app;

--
-- Name: schema_migrations; Type: TABLE; Schema: public; Owner: fatty_app
--

CREATE TABLE public.schema_migrations (
    version integer NOT NULL,
    applied_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


ALTER TABLE public.schema_migrations OWNER TO fatty_app;

--
-- Name: source_management_provider_intents; Type: TABLE; Schema: public; Owner: fatty_app
--

CREATE TABLE public.source_management_provider_intents (
    management_update_id uuid NOT NULL,
    client_order_id text NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


ALTER TABLE public.source_management_provider_intents OWNER TO fatty_app;

--
-- Name: source_management_updates; Type: TABLE; Schema: public; Owner: fatty_app
--

CREATE TABLE public.source_management_updates (
    id uuid NOT NULL,
    source_message_id uuid NOT NULL,
    revision text NOT NULL,
    symbol text NOT NULL,
    action text NOT NULL,
    state text NOT NULL,
    claimed_by text,
    claimed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT source_management_updates_action_check CHECK ((action = ANY (ARRAY['TP1_BOOKED'::text, 'SL_TO_ENTRY'::text, 'CLOSE'::text]))),
    CONSTRAINT source_management_updates_state_check CHECK ((state = ANY (ARRAY['queued'::text, 'claimed'::text, 'reconciliation-pending'::text, 'failed'::text, 'reconciled'::text])))
);


ALTER TABLE public.source_management_updates OWNER TO fatty_app;

--
-- Name: telegram_messages; Type: TABLE; Schema: public; Owner: fatty_app
--

CREATE TABLE public.telegram_messages (
    id uuid NOT NULL,
    channel_id bigint NOT NULL,
    message_id bigint NOT NULL,
    revision_hash character(64) NOT NULL,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    raw_text text NOT NULL,
    intake_state text NOT NULL,
    has_media boolean DEFAULT false NOT NULL,
    media_path text,
    media_sha256 character(64),
    media_mime_type text,
    media_size_bytes integer,
    CONSTRAINT telegram_messages_intake_state_check CHECK ((intake_state = ANY (ARRAY['RECEIVED'::text, 'ANALYZED'::text, 'FAILED'::text, 'EXPIRED'::text])))
);


ALTER TABLE public.telegram_messages OWNER TO fatty_app;

--
-- Name: venue_kill_switches; Type: TABLE; Schema: public; Owner: fatty_app
--

CREATE TABLE public.venue_kill_switches (
    scope text NOT NULL,
    active boolean DEFAULT false NOT NULL,
    reason text,
    latched_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


ALTER TABLE public.venue_kill_switches OWNER TO fatty_app;

--
-- Data for Name: balance_snapshots; Type: TABLE DATA; Schema: public; Owner: fatty_app
--

COPY public.balance_snapshots (id, exchange, total_balance, available_balance, equity, margin_coin, captured_at) FROM stdin;
\.


--
-- Data for Name: bitget_protection_capabilities; Type: TABLE DATA; Schema: public; Owner: fatty_app
--

COPY public.bitget_protection_capabilities (exchange, environment, symbol, position_mode, margin_mode, native_state, fallback_allowed, payload_profile, last_verified_at, last_error, stream_state, last_stream_at, updated_at) FROM stdin;
bitget	LIVE	SNDKUSDT	one_way_mode	isolated	UNSUPPORTED	t	classic-v2-position	\N	protection-stream-stale	STALE	\N	2026-09-15 23:09:26.679968+00
bitget	LIVE	WLDUSDT	one_way_mode	isolated	UNSUPPORTED	t	classic-v2-position	\N	native-protection-unsupported	STALE	\N	2026-09-19 18:36:54.812586+00
\.


--
-- Data for Name: canary_entry_reservations; Type: TABLE DATA; Schema: public; Owner: fatty_app
--

COPY public.canary_entry_reservations (dispatch_id, exchange, created_at) FROM stdin;
e0000000-0000-0000-0000-000000000001	bitget	2026-09-11 09:40:34.947361+00
23c8dc14-4553-454f-8eed-247da9e50b56	bitget	2026-09-12 11:42:00.459254+00
7ecac60d-696d-4dcb-8fe3-34f3307ad596	bitget	2026-09-15 16:17:44.35445+00
\.


--
-- Data for Name: canonical_signals; Type: TABLE DATA; Schema: public; Owner: fatty_app
--

COPY public.canonical_signals (id, message_id, revision, pair_token, direction, entry_price, stop_loss, created_at, take_profits) FROM stdin;
f6f95b16-2275-4386-8e3e-c12ac573c6cf	c8533589-43a0-460e-88f7-09601f81d252	ed173cc7907464c767074ec05e2579b5bf33fc60f9a726663936d08b51793297	PUMP	LONG	0.00427	0.00416	2026-09-06 14:10:56.813511+00	["0.004438", "0.004915"]
cdcad637-9b6b-4082-84df-bcbb07482fee	5714ab4a-772b-420e-84b4-3759d8ccdac3	d6e75e6b2802d2b7425a2f6687d092b91c5a80c83c63531474003d33a9e5c9e2	SUSHI	LONG	0.2512	0.2428	2026-09-06 15:43:13.744644+00	["0.2835"]
182223ae-08ae-491e-a254-2627779f1375	a81098c7-eca3-4861-a937-824cf2ee8cf9	31ad0cd230efe4e6b0c18e274ea9a58ba6794f5ecad6d4cb89715908e7851ff6	NOT	LONG	0.0004715	0.000458	2026-09-07 13:55:03.479459+00	["0.00058"]
2b54f488-ffc7-4652-a6b4-34c373e34cb4	854d4686-8f42-48ef-9997-a174886c7718	3f8ed5920fbd14b5b6afd0c1137ebc2797aa5634d286747c6995e2e8b20f1e9d	WLD	LONG	0.47	0.4562	2026-09-08 14:06:08.021238+00	["0.51"]
7ec702aa-b83a-4232-a446-7fb2cebdf632	cd4f924e-26b5-44a5-9458-b55d98cd2fa5	16cf7ebc3bf041f9d21f79acbe944f89e8e089c3244dafc4ebbe43420e8e2f8f	FLOKI	LONG	0.02665	0.02587	2026-09-08 16:45:45.832345+00	["0.0304"]
9229d113-9245-4beb-a01f-5400726b43a8	81fe0d12-d6d3-46d3-ac1b-83609ad149e6	e94dec6e9971b34e9b5566a5b16e783d418fbd2fbfe0853ff2ab6c817092212a	WLD	LONG	0.4495	0.4345	2026-09-09 13:48:01.778903+00	["0.491"]
cde00049-479f-47a8-9090-9187fd2205ac	93031ac9-89d0-4107-8685-0395dafc9d64	a405cd0f2ffd422e240616f5543e64ba03d5cfd9369896908db7c7be985766b9	GRASS	LONG	0.3505	0.3382	2026-09-10 17:25:57.567816+00	["0.414"]
b0000000-0000-0000-0000-000000000001	a0000000-0000-0000-0000-000000000001	0                                                               	GRASS	LONG	0.34690	0.34400	2026-09-11 09:03:08.342879+00	[0.34960, 0.35290]
d0000000-0000-0000-0000-000000000001	a0000000-0000-0000-0000-000000000001	64                                                              	GRASS	LONG	0.34690	0.34400	2026-09-11 09:31:24.220813+00	[0.34960, 0.35290]
5e604d01-cecd-4235-9492-2c383a8d2955	966d30d5-26fd-48c7-adb9-ba8dabffdbe3	4dd1c1b34fe2310ae1fb634d8cd6a07e1999a5e4312c4c071b5451197004a418	INJ	LONG	5.835	5.66	2026-09-11 18:27:44.546318+00	["6.72"]
f8f504e1-f15c-4625-be04-b529b3d810c5	561dc43c-ecea-4701-8276-633ea8d47f00	81d987b81e65a91aa9abdc4726f6a09e83cac4cbe45514eec2baae970c3a1068	ETHFI	LONG	0.751	0.718	2026-09-12 11:41:19.952743+00	["0.87"]
25ac52f1-1932-4516-958b-1acacdab0e0f	ffafa787-e92a-47f7-adbb-6379dd0045aa	23c1c4c4a60a7ce4514b67b894d4c468aadc9718efe9466e0b2c980640731637	EUL	LONG	1.268	1.2365	2026-09-12 17:31:16.618112+00	["1.47"]
6bbee916-a466-4862-b865-195f4eb77b41	e7f89b7d-e8da-43c3-872a-d932564c0419	311a40ff51b8abb56a5cfd11ee6734814b5cb2b19ddc4b52322f8b2715dd2159	PONS	LONG	0.584	0.564	2026-09-13 03:48:08.35757+00	["0.747"]
8445c4f2-1369-596c-8f8d-a07124bfd6b0	ac5ebb01-8a3b-40af-bb84-c1145d204598	3886302802ed718b40cec19aa66a88b18552b7970089e48fdfa08275e0bd5f77	WLD	LONG	0.396	0.388	2026-09-13 14:28:14.235515+00	["0.427"]
a0e66411-f545-5dd1-b8f2-906782fc3163	f2c137d9-871a-426b-92c1-9094c46d6dbb	1475a9a58b03a4864980b730227894efff93e6ba2d29a3a95129b0fde6bf79c6	WLD	SHORT	0.385	0.3938	2026-09-14 22:38:31.480353+00	[]
7ddd16f0-ff63-5a56-ad27-07df7e22a921	d5fab280-0de5-4050-a55d-c36ded76cd58	959de2ad5273b8f419be00253738cdcdf16498efb34cd8bbdd516a8f23209770	SNDK	LONG	1560	1535	2026-09-15 14:09:27.432029+00	["1795"]
e49c5a39-cbe3-58b8-86b4-7f779f33d2e3	c9cfc94a-d500-42c8-b12f-16bc9172c129	9e3b29dc3e0bcef60779c4152cd313676c69c065307fb474a9d9974a851bcea9	TAO	LONG	224.5	218.55	2026-09-15 16:17:30.623636+00	["236.9"]
64574a60-2c3c-5c77-b931-d3456848e934	7328ff35-0eb0-4cce-95bb-f2ee00aae80f	84f7c9437aaca5f320aa9c4f9ebd02870c82fdf3538d8572c0f0eb5e6b04634b	PENDLE	LONG	2.32	2.2465	2026-09-17 20:25:04.654028+00	["3.22"]
3ec714d3-f171-565c-ae0f-8a676f7f4262	d17659ad-c1ee-4c2d-b49f-aa894246ddf3	a888af4db7c17b1ab1776498824f297f1c2fe03c5dced816436dc4df23710259	SAGA	LONG	0.0221	0.02112	2026-09-18 02:23:19.787173+00	["0.036"]
88967b6a-490a-5f40-bad6-33d0a5c669b1	f0e2b989-749b-4fc0-a0ac-ddb6499e8d12	4ad403a92954d6205b0c300e74fd0d67c5b371b7869f5c9a08a8de71910c4455	WLD	LONG	0.431	0.4195	2026-09-19 18:36:41.625399+00	["0.46"]
ad550c19-9fdb-5cad-9bc6-d3f33a78f397	43946e25-f0fe-4914-973c-60d3afcfd3c2	a895d62692bfc5685b0294f2e8d1d5584c5228d3912d85d8ad5a0bb1c5d89187	BONK	LONG	0.003015	0.002935	2026-09-20 17:19:35.304109+00	["0.00333"]
\.


--
-- Data for Name: dispatch_transitions; Type: TABLE DATA; Schema: public; Owner: fatty_app
--

COPY public.dispatch_transitions (id, dispatch_id, from_state, to_state, reason, created_at) FROM stdin;
59d4c171-9ac0-44bb-8fbf-0cb29bface07	09358d64-e2ba-4ef5-942e-3198d9476289	QUEUED	REJECTED	kill-switch-latched	2026-09-06 14:11:18.902954+00
775a310d-e4f0-475f-a99f-e722ea2e5ee2	4a36d170-be56-4014-ae6b-8b4e86009e7d	QUEUED	REJECTED	kill-switch-latched	2026-09-06 15:43:42.961069+00
cee9a2df-0b3c-4069-b3f1-0feb04f58244	09358d64-e2ba-4ef5-942e-3198d9476289	REJECTED	QUEUED	requeued-after-demo-kill-switch-recovery	2026-09-07 01:12:49.270963+00
9a13575a-6a2f-4019-b5d3-6c565a646132	4a36d170-be56-4014-ae6b-8b4e86009e7d	REJECTED	QUEUED	requeued-after-demo-kill-switch-recovery	2026-09-07 01:12:49.270963+00
8c223a08-a815-465e-b563-3089d5fefd35	09358d64-e2ba-4ef5-942e-3198d9476289	QUEUED	PREFLIGHT	\N	2026-09-07 01:12:51.448791+00
6b7ee788-fb81-4626-98e8-3b81648d243f	4a36d170-be56-4014-ae6b-8b4e86009e7d	QUEUED	PREFLIGHT	\N	2026-09-07 01:12:53.157467+00
466f6824-e9f7-41e4-b050-175b9ec2a335	09358d64-e2ba-4ef5-942e-3198d9476289	QUEUED	REJECTED	cutover-gated	2026-09-07 01:30:03.019158+00
297d036d-8fb9-460d-8dd5-0618b88d8818	4a36d170-be56-4014-ae6b-8b4e86009e7d	QUEUED	REJECTED	cutover-gated	2026-09-07 01:30:33.143341+00
36763407-d384-452f-bd1d-6b16ecbb0b89	38d46484-b312-4fff-8d70-7bf6083fbdca	QUEUED	REJECTED	engine-disabled	2026-09-07 15:53:58.584595+00
2e72875a-6e00-429a-a9b2-7a84ca0d9674	c5304f50-08d5-4b39-b78d-104994498989	QUEUED	REJECTED	engine-disabled	2026-09-07 15:53:58.584595+00
8b287a53-3781-4c7b-acbb-9e4077e1bba4	334b6ffc-855c-4549-a4f7-7ce145ecc9cb	QUEUED	REJECTED	engine-disabled	2026-09-07 15:53:58.584595+00
67333299-ac0c-4a76-bcab-979540292ec4	7136e226-e812-4862-b009-7f0f0c688580	QUEUED	PREFLIGHT	\N	2026-09-08 03:46:24.88788+00
fdc68758-9155-4694-b6c7-ad652fafa0f9	7136e226-e812-4862-b009-7f0f0c688580	PREFLIGHT	SIZED	\N	2026-09-08 03:46:26.587374+00
a00b9ba3-c69c-45ab-8a36-6aa19ac9205b	7136e226-e812-4862-b009-7f0f0c688580	SIZED	VALIDATED	\N	2026-09-08 03:46:26.602207+00
0fa843aa-d612-4a36-a937-7c6a62faba38	7136e226-e812-4862-b009-7f0f0c688580	VALIDATED	SUBMITTING	\N	2026-09-08 03:46:26.632862+00
0440ab61-1a0b-4464-a001-1a9249cbb219	7136e226-e812-4862-b009-7f0f0c688580	SUBMITTING	UNKNOWN	provider-unknown	2026-09-08 03:46:29.432388+00
9135b05b-277a-4ebd-b90e-0ffbb2519ddd	577ed2b8-9511-4abc-851b-2f2d256714bf	QUEUED	REJECTED	cutover-gated	2026-09-08 14:06:38.677353+00
7ee8dea5-9598-4d2b-ab4a-142121ef15c1	9a7c19c1-c834-491d-9f15-b787b02fe0ab	QUEUED	REJECTED	cutover-gated	2026-09-08 16:46:19.218185+00
82cb15c7-0f47-41b8-b613-c4dad37cab8c	7136e226-e812-4862-b009-7f0f0c688580	UNKNOWN	FILLED	historical-entry-filled-and-provider-flat-reconciled	2026-09-08 17:00:36.259743+00
dea0630d-e8d1-4c31-8d16-2a736f4f4e77	6072e55e-dd0c-4b86-b716-04dbfcb31f6f	QUEUED	PREFLIGHT	\N	2026-09-09 13:48:24.155876+00
f32abdc0-dbe2-4f57-9c44-84ca68967dd1	6072e55e-dd0c-4b86-b716-04dbfcb31f6f	PREFLIGHT	SIZED	\N	2026-09-09 13:48:26.548522+00
208876ac-c807-40b4-860a-475f4ae511ad	6072e55e-dd0c-4b86-b716-04dbfcb31f6f	SIZED	VALIDATED	\N	2026-09-09 13:48:26.594699+00
1a3b388d-fc2f-477a-81e2-98e14d6e4837	6072e55e-dd0c-4b86-b716-04dbfcb31f6f	VALIDATED	SUBMITTING	\N	2026-09-09 13:48:26.645775+00
f3470724-e2ad-4061-a899-456bb88e98b7	6072e55e-dd0c-4b86-b716-04dbfcb31f6f	SUBMITTING	UNKNOWN	provider-unknown	2026-09-09 13:48:29.640905+00
2d2bd5ac-e97b-4b73-b1b2-e66b44e20110	1b14b580-8360-4797-a95a-d0015b0acd2f	QUEUED	PREFLIGHT	\N	2026-09-10 17:26:23.075736+00
f664bb6c-6e8e-40ae-9bbc-034464323966	1b14b580-8360-4797-a95a-d0015b0acd2f	PREFLIGHT	REJECTED	Bitget account margin mode must be isolated	2026-09-10 17:26:24.372272+00
4addfaa6-50e6-4cb0-bfd0-d21b51bb5fa1	c0000000-0000-0000-0000-000000000001	QUEUED	PREFLIGHT	\N	2026-09-11 09:06:28.591338+00
e01e137e-1e9b-4daa-9a3d-ec47cb7bad24	c0000000-0000-0000-0000-000000000001	PREFLIGHT	SIZED	\N	2026-09-11 09:06:30.030401+00
1a9c66a3-6c74-4cee-a2e3-4885ecb39dc6	c0000000-0000-0000-0000-000000000001	SIZED	VALIDATED	\N	2026-09-11 09:06:30.05289+00
e42e6eda-126d-4c9c-a775-9ac6e6fc370f	c0000000-0000-0000-0000-000000000001	VALIDATED	SUBMITTING	\N	2026-09-11 09:06:30.102134+00
f00a3f18-a2cf-42fb-9252-f55a3b87bb0b	c0000000-0000-0000-0000-000000000001	SUBMITTING	UNKNOWN	provider-unknown	2026-09-11 09:06:33.191757+00
c7dfb8de-d35b-4e1c-8a42-502097f381c1	e0000000-0000-0000-0000-000000000001	QUEUED	PREFLIGHT	\N	2026-09-11 09:40:33.569642+00
c22ed616-dcb3-4189-9858-5b84808b179f	e0000000-0000-0000-0000-000000000001	PREFLIGHT	SIZED	\N	2026-09-11 09:40:34.915024+00
08f55530-3cad-4453-ba2d-a2a363ccc7f6	e0000000-0000-0000-0000-000000000001	SIZED	VALIDATED	\N	2026-09-11 09:40:34.931911+00
71ae35a9-a32f-4165-8092-24c6e0340eb1	e0000000-0000-0000-0000-000000000001	VALIDATED	SUBMITTING	\N	2026-09-11 09:40:34.961533+00
a85f0619-2cbb-44b4-b757-834ef186df95	e0000000-0000-0000-0000-000000000001	SUBMITTING	FILLED	reconciled-post-degraded	2026-09-11 09:48:12.822024+00
44367f01-9b92-4000-8ae7-9389b77ed76f	02de5cb4-8e11-4916-929b-6d95c1b51b1f	QUEUED	PREFLIGHT	\N	2026-09-11 18:28:06.210148+00
b4fd363a-79db-4b7d-bccf-d77cb3362210	02de5cb4-8e11-4916-929b-6d95c1b51b1f	PREFLIGHT	SIZED	\N	2026-09-11 18:28:08.245709+00
401d88a4-9530-4c52-ad8a-9d4cb93d7048	02de5cb4-8e11-4916-929b-6d95c1b51b1f	SIZED	VALIDATED	\N	2026-09-11 18:28:08.264103+00
ebc97c75-8050-45a3-82c2-7df45846f543	02de5cb4-8e11-4916-929b-6d95c1b51b1f	VALIDATED	SUBMITTING	\N	2026-09-11 18:28:08.306725+00
5002f231-3f39-4dfc-8c84-5727bbbe6986	02de5cb4-8e11-4916-929b-6d95c1b51b1f	SUBMITTING	UNKNOWN	provider-unknown	2026-09-11 18:28:11.077588+00
b65cf6f1-81f9-4089-a064-8ee09f385263	23c8dc14-4553-454f-8eed-247da9e50b56	QUEUED	PREFLIGHT	\N	2026-09-12 11:41:57.895493+00
a0e3b6d7-8597-4eb3-802c-2c5e2409a7e3	23c8dc14-4553-454f-8eed-247da9e50b56	PREFLIGHT	SIZED	\N	2026-09-12 11:42:00.424831+00
0652f1b9-4585-4887-99b4-ababd1d2b164	23c8dc14-4553-454f-8eed-247da9e50b56	SIZED	VALIDATED	\N	2026-09-12 11:42:00.443481+00
bbb5d1d1-af10-4e55-94ce-425b68bcce94	23c8dc14-4553-454f-8eed-247da9e50b56	VALIDATED	SUBMITTING	\N	2026-09-12 11:42:00.484369+00
1a088cf2-93ca-4dbd-b1f8-cef8c93cf765	23c8dc14-4553-454f-8eed-247da9e50b56	SUBMITTING	UNKNOWN	provider-unknown	2026-09-12 11:42:03.04649+00
5e6f76a7-6c8d-449d-bde1-87b26f079170	9ea05801-e066-409a-8817-83cac6dfc638	QUEUED	PREFLIGHT	\N	2026-09-12 17:31:35.082227+00
34ae702b-068d-4c1f-98b1-ca5753c1d5bc	9ea05801-e066-409a-8817-83cac6dfc638	PREFLIGHT	SIZED	\N	2026-09-12 17:31:37.571449+00
49aa0841-0928-45e7-a986-c6fb2caf0cac	9ea05801-e066-409a-8817-83cac6dfc638	SIZED	VALIDATED	\N	2026-09-12 17:31:37.590601+00
5e609af0-c174-45cf-8956-f9ca811ddd21	9ea05801-e066-409a-8817-83cac6dfc638	VALIDATED	SUBMITTING	\N	2026-09-12 17:31:37.626353+00
7152c9fd-e5f1-4f55-96f8-4dfd9eac507f	9ea05801-e066-409a-8817-83cac6dfc638	SUBMITTING	UNKNOWN	provider-unknown	2026-09-12 17:31:40.171946+00
17af0309-11be-4e48-9620-0eb7f7698290	9e9516b4-c2de-4bf2-954c-73d5a41f3310	QUEUED	PREFLIGHT	\N	2026-09-13 03:48:33.83587+00
27442fed-665c-4b45-bae3-4815bc4a4560	9e9516b4-c2de-4bf2-954c-73d5a41f3310	PREFLIGHT	SIZED	\N	2026-09-13 03:48:36.188002+00
537a5b10-b6a2-4631-90d7-5bd338602787	9e9516b4-c2de-4bf2-954c-73d5a41f3310	SIZED	VALIDATED	\N	2026-09-13 03:48:36.209878+00
fbf95cea-5bbb-4083-9d0b-4c7a5c738fb8	9e9516b4-c2de-4bf2-954c-73d5a41f3310	VALIDATED	REJECTED	canary-order-cap-reached	2026-09-13 03:48:36.248357+00
33e6216f-d8c8-42e0-9eae-55706af2144d	8d61b0c7-1cfe-4d01-8503-8a5394f9dc71	QUEUED	PREFLIGHT	\N	2026-09-13 14:28:30.495216+00
878a394b-8f2a-4ea6-a08f-31496efb1811	8d61b0c7-1cfe-4d01-8503-8a5394f9dc71	PREFLIGHT	SIZED	\N	2026-09-13 14:28:32.493068+00
7bd8aa0f-7068-4ff4-b693-ebf4713e1a4e	8d61b0c7-1cfe-4d01-8503-8a5394f9dc71	SIZED	VALIDATED	\N	2026-09-13 14:28:32.509082+00
737e75ae-fa83-4f49-97a9-2983168ab9bf	8d61b0c7-1cfe-4d01-8503-8a5394f9dc71	VALIDATED	SUBMITTING	\N	2026-09-13 14:28:32.544742+00
47d6cd64-bf9e-46bc-9aca-a3e289775ca0	8d61b0c7-1cfe-4d01-8503-8a5394f9dc71	SUBMITTING	UNKNOWN	provider-unknown	2026-09-13 14:28:35.309731+00
716f33db-4880-4af0-84cd-9c60354f9ad6	8d61b0c7-1cfe-4d01-8503-8a5394f9dc71	UNKNOWN	FILLED	entry-filled-fallback-protection-active	2026-09-13 14:45:30.609448+00
f6654502-b749-4283-ab1b-516d0ceb6a15	c0000000-0000-0000-0000-000000000001	UNKNOWN	RECONCILED	approved-provider-flat-readback-no-order-or-intent	2026-09-14 06:39:20.588724+00
7b6a7f3b-c7b4-4464-aea2-3c448d2bb2a3	02de5cb4-8e11-4916-929b-6d95c1b51b1f	UNKNOWN	RECONCILED	approved-provider-flat-readback-no-order-or-intent	2026-09-14 06:39:20.618925+00
26ef025d-d895-45a4-88f0-acb486897d1f	9ea05801-e066-409a-8817-83cac6dfc638	UNKNOWN	RECONCILED	approved-provider-flat-readback-no-order-or-intent	2026-09-14 06:39:20.64466+00
8331e9ab-932a-4b56-b16a-096ff340820c	e92ed413-f6c0-4eb4-877a-d0e3840021be	QUEUED	REJECTED	missing-take-profits	2026-09-14 22:39:01.924439+00
c676ca6e-a2fa-4982-b998-e53aa020950d	e92ed413-f6c0-4eb4-877a-d0e3840021be	QUEUED	PREFLIGHT	\N	2026-09-14 22:42:28.488447+00
8f29c81d-addb-48b3-9c31-9ef2cb3f9006	e92ed413-f6c0-4eb4-877a-d0e3840021be	PREFLIGHT	SIZED	\N	2026-09-14 22:42:29.878031+00
4f56ced6-3502-4a01-8f23-84953414d4f0	e92ed413-f6c0-4eb4-877a-d0e3840021be	SIZED	VALIDATED	\N	2026-09-14 22:42:29.893984+00
a87bdbeb-893b-4306-adaf-a9ab22184ab6	e92ed413-f6c0-4eb4-877a-d0e3840021be	VALIDATED	SUBMITTING	\N	2026-09-14 22:42:29.927554+00
623b9ae6-f77c-44ae-bb5e-1ae2638d5df0	e92ed413-f6c0-4eb4-877a-d0e3840021be	SUBMITTING	FILLED	fallback-protection-active	2026-09-14 22:42:32.344604+00
80416123-3689-469e-9588-9bbb6d67b4bf	a12e4cfe-9e88-4406-8e18-6cce9549e1b7	QUEUED	REJECTED	kill-switch-latched	2026-09-15 14:09:56.298595+00
fedc7ce0-3f1e-4013-896e-26c65785ae00	a12e4cfe-9e88-4406-8e18-6cce9549e1b7	REJECTED	QUEUED	requeued-after-alert-only-protection-policy	2026-09-15 14:27:25.913622+00
17878cee-9def-4021-a102-62be2ace1ee8	a12e4cfe-9e88-4406-8e18-6cce9549e1b7	QUEUED	PREFLIGHT	\N	2026-09-15 14:27:26.837154+00
68a37251-b139-43c4-9ebb-e35928792205	a12e4cfe-9e88-4406-8e18-6cce9549e1b7	PREFLIGHT	SIZED	\N	2026-09-15 14:27:28.916585+00
30c8b2f6-27e3-429c-8d3e-c865724f33e8	a12e4cfe-9e88-4406-8e18-6cce9549e1b7	SIZED	VALIDATED	\N	2026-09-15 14:27:28.929845+00
0bd86d7d-f235-4686-b8b0-49e599f97c42	a12e4cfe-9e88-4406-8e18-6cce9549e1b7	VALIDATED	SUBMITTING	\N	2026-09-15 14:27:28.960561+00
0a525b3e-dfc4-46bf-8171-aae1214997a3	a12e4cfe-9e88-4406-8e18-6cce9549e1b7	SUBMITTING	FILLED	fallback-protection-active	2026-09-15 14:27:31.508046+00
bcded9a3-23e7-413e-92aa-24b3b6a0bceb	7ecac60d-696d-4dcb-8fe3-34f3307ad596	QUEUED	PREFLIGHT	\N	2026-09-15 16:17:42.37052+00
948d152a-d8cd-4e47-9a68-7bf534c4e680	7ecac60d-696d-4dcb-8fe3-34f3307ad596	PREFLIGHT	SIZED	\N	2026-09-15 16:17:44.307913+00
87894223-16b8-4e86-b158-b27e36b7eb1f	7ecac60d-696d-4dcb-8fe3-34f3307ad596	SIZED	VALIDATED	\N	2026-09-15 16:17:44.331595+00
cc5926b3-7624-48fc-a4e9-f035bba84854	7ecac60d-696d-4dcb-8fe3-34f3307ad596	VALIDATED	SUBMITTING	\N	2026-09-15 16:17:44.375735+00
c608a8e3-2a17-465c-b144-badf25e69a26	7ecac60d-696d-4dcb-8fe3-34f3307ad596	SUBMITTING	UNKNOWN	provider-readback-error:RuntimeError	2026-09-15 16:17:44.407651+00
800c98e0-eb4e-4ee0-9106-864a947f2526	7ecac60d-696d-4dcb-8fe3-34f3307ad596	UNKNOWN	REJECTED	provider-flat-no-order-or-fill	2026-09-15 20:20:11.957717+00
9a8e8be5-9103-4093-9bcc-c76c73d795e3	ff9a5f40-d9a3-40aa-984e-8628902c504b	QUEUED	PREFLIGHT	\N	2026-09-17 20:25:39.752514+00
88694a13-bfd5-4b5e-a647-01f57eae68ab	ff9a5f40-d9a3-40aa-984e-8628902c504b	PREFLIGHT	REJECTED	3 validation errors for VenueRiskConfig\nbase_margin_usdt\n  Input should be greater than 0 [type=greater_than, input_value=Decimal('0.00'), input_type=Decimal]\n    For further information visit https://errors.pydantic.dev/2.13/v/greater_than\nmax_auto_margin_usdt\n  Input should be greater than 0 [type=greater_than, input_value=Decimal('0.00'), input_type=Decimal]\n    For further information visit https://errors.pydantic.dev/2.13/v/greater_than\nmax_position_notional_usdt\n  Input should be greater than 0 [type=greater_than, input_value=Decimal('0.00'), input_type=Decimal]\n    For further information visit https://errors.pydantic.dev/2.13/v/greater_than	2026-09-17 20:25:42.420253+00
be56550c-7f6b-4fdf-8cfb-9672e67d287b	259b562d-7834-44db-8c6f-9cd2605b8c15	QUEUED	PREFLIGHT	\N	2026-09-18 02:24:13.407705+00
8718bcb7-e31d-4aaa-abf0-002fe12f681c	259b562d-7834-44db-8c6f-9cd2605b8c15	PREFLIGHT	REJECTED	Bitget GET /api/v2/mix/account/account HTTP 400: 40309 The symbol has been removed	2026-09-18 02:24:14.329792+00
609622eb-6a07-4322-a85d-15f14cebd981	cb58a3cb-5c50-47f5-8228-306ebca6e813	QUEUED	PREFLIGHT	\N	2026-09-19 18:36:50.233444+00
1358368e-1b8b-4c18-853e-1dbbd807df4c	cb58a3cb-5c50-47f5-8228-306ebca6e813	PREFLIGHT	SIZED	\N	2026-09-19 18:36:52.121867+00
37d1d2f8-ab24-4bfe-9227-41e70e8d5ddf	cb58a3cb-5c50-47f5-8228-306ebca6e813	SIZED	VALIDATED	\N	2026-09-19 18:36:52.151781+00
85257bde-fdb3-4f97-8432-3d7863e81e2e	cb58a3cb-5c50-47f5-8228-306ebca6e813	VALIDATED	SUBMITTING	\N	2026-09-19 18:36:52.194312+00
382f559a-8c9c-4db3-8aed-953e21f72af0	cb58a3cb-5c50-47f5-8228-306ebca6e813	SUBMITTING	FILLED	fallback-protection-active	2026-09-19 18:36:54.901293+00
d21ba8f1-ee10-43b2-a522-eeb9d5e84d7f	9c6d5119-4041-4951-af84-d9a2fceaeee6	QUEUED	PREFLIGHT	\N	2026-09-20 17:19:46.015302+00
6a117264-fdc1-4ed0-b2fd-55100811a0ed	9c6d5119-4041-4951-af84-d9a2fceaeee6	PREFLIGHT	REJECTED	Bitget GET /api/v2/mix/account/account HTTP 400: 40034 Parameter BONKUSDT does not exist	2026-09-20 17:19:46.632536+00
\.


--
-- Data for Name: dispatches; Type: TABLE DATA; Schema: public; Owner: fatty_app
--

COPY public.dispatches (id, source_type, source_id, revision, exchange, state, claimed_by, lease_until, attempts, terminal_reason, created_at, updated_at) FROM stdin;
e92ed413-f6c0-4eb4-877a-d0e3840021be	canonical_signal	a0e66411-f545-5dd1-b8f2-906782fc3163	1475a9a58b03a4864980b730227894efff93e6ba2d29a3a95129b0fde6bf79c6	bitget	FILLED	dispatcher-bitget	2026-09-14 22:42:58.45959+00	2	fallback-protection-active	2026-09-14 22:38:31.480353+00	2026-09-14 22:42:32.344604+00
8d61b0c7-1cfe-4d01-8503-8a5394f9dc71	canonical_signal	8445c4f2-1369-596c-8f8d-a07124bfd6b0	3886302802ed718b40cec19aa66a88b18552b7970089e48fdfa08275e0bd5f77	bitget	FILLED	dispatcher-bitget	2026-09-13 14:29:00.414341+00	1	entry-filled-fallback-protection-active	2026-09-13 14:28:14.235515+00	2026-09-13 14:45:30.609448+00
c0000000-0000-0000-0000-000000000001	canonical_signal	b0000000-0000-0000-0000-000000000001	0                                                               	bitget	RECONCILED	dispatcher-bitget	2026-09-11 09:06:58.56867+00	2	approved-provider-flat-readback-no-order-or-intent	2026-09-11 09:03:08.342879+00	2026-09-14 06:39:20.588724+00
e0000000-0000-0000-0000-000000000001	canonical_signal	d0000000-0000-0000-0000-000000000001	64                                                              	bitget	FILLED	dispatcher-bitget	2026-09-11 09:41:03.551734+00	2	entry-filled-protection-failed-emergency-close-executed	2026-09-11 09:31:24.220813+00	2026-09-11 09:48:12.822024+00
577ed2b8-9511-4abc-851b-2f2d256714bf	canonical_signal	2b54f488-ffc7-4652-a6b4-34c373e34cb4	3f8ed5920fbd14b5b6afd0c1137ebc2797aa5634d286747c6995e2e8b20f1e9d	bitget	REJECTED	dispatcher-bitget	2026-09-08 14:07:08.628237+00	1	cutover-gated	2026-09-08 14:06:08.021238+00	2026-09-08 14:06:38.677353+00
02de5cb4-8e11-4916-929b-6d95c1b51b1f	canonical_signal	5e604d01-cecd-4235-9492-2c383a8d2955	4dd1c1b34fe2310ae1fb634d8cd6a07e1999a5e4312c4c071b5451197004a418	bitget	RECONCILED	dispatcher-bitget	2026-09-11 18:28:36.179048+00	1	approved-provider-flat-readback-no-order-or-intent	2026-09-11 18:27:44.546318+00	2026-09-14 06:39:20.618925+00
09358d64-e2ba-4ef5-942e-3198d9476289	canonical_signal	f6f95b16-2275-4386-8e3e-c12ac573c6cf	ed173cc7907464c767074ec05e2579b5bf33fc60f9a726663936d08b51793297	bitget	REJECTED	dispatcher-bitget	2026-09-07 01:30:32.959156+00	3	cutover-gated	2026-09-06 14:10:56.813511+00	2026-09-07 01:30:03.019158+00
9a7c19c1-c834-491d-9f15-b787b02fe0ab	canonical_signal	7ec702aa-b83a-4232-a446-7fb2cebdf632	16cf7ebc3bf041f9d21f79acbe944f89e8e089c3244dafc4ebbe43420e8e2f8f	bitget	REJECTED	dispatcher-bitget	2026-09-08 16:46:49.188176+00	1	cutover-gated	2026-09-08 16:45:45.832345+00	2026-09-08 16:46:19.218185+00
4a36d170-be56-4014-ae6b-8b4e86009e7d	canonical_signal	cdcad637-9b6b-4082-84df-bcbb07482fee	d6e75e6b2802d2b7425a2f6687d092b91c5a80c83c63531474003d33a9e5c9e2	bitget	REJECTED	dispatcher-bitget	2026-09-07 01:31:03.117188+00	3	cutover-gated	2026-09-06 15:43:13.744644+00	2026-09-07 01:30:33.143341+00
7136e226-e812-4862-b009-7f0f0c688580	canonical_signal	182223ae-08ae-491e-a254-2627779f1375	31ad0cd230efe4e6b0c18e274ea9a58ba6794f5ecad6d4cb89715908e7851ff6	bitget	FILLED	dispatcher-bitget	2026-09-08 03:46:54.844764+00	1	historical-entry-filled-and-provider-flat-reconciled	2026-09-07 13:55:03.479459+00	2026-09-08 17:00:36.259743+00
38d46484-b312-4fff-8d70-7bf6083fbdca	canonical_signal	f6f95b16-2275-4386-8e3e-c12ac573c6cf	ed173cc7907464c767074ec05e2579b5bf33fc60f9a726663936d08b51793297	binance	REJECTED	\N	\N	0	engine-disabled	2026-09-06 14:10:56.813511+00	2026-09-07 15:53:58.584595+00
c5304f50-08d5-4b39-b78d-104994498989	canonical_signal	cdcad637-9b6b-4082-84df-bcbb07482fee	d6e75e6b2802d2b7425a2f6687d092b91c5a80c83c63531474003d33a9e5c9e2	binance	REJECTED	\N	\N	0	engine-disabled	2026-09-06 15:43:13.744644+00	2026-09-07 15:53:58.584595+00
334b6ffc-855c-4549-a4f7-7ce145ecc9cb	canonical_signal	182223ae-08ae-491e-a254-2627779f1375	31ad0cd230efe4e6b0c18e274ea9a58ba6794f5ecad6d4cb89715908e7851ff6	binance	REJECTED	\N	\N	0	engine-disabled	2026-09-07 13:55:03.479459+00	2026-09-07 15:53:58.584595+00
9ea05801-e066-409a-8817-83cac6dfc638	canonical_signal	25ac52f1-1932-4516-958b-1acacdab0e0f	23c1c4c4a60a7ce4514b67b894d4c468aadc9718efe9466e0b2c980640731637	bitget	RECONCILED	dispatcher-bitget	2026-09-12 17:32:05.029756+00	1	approved-provider-flat-readback-no-order-or-intent	2026-09-12 17:31:16.618112+00	2026-09-14 06:39:20.64466+00
6072e55e-dd0c-4b86-b716-04dbfcb31f6f	canonical_signal	9229d113-9245-4beb-a01f-5400726b43a8	e94dec6e9971b34e9b5566a5b16e783d418fbd2fbfe0853ff2ab6c817092212a	bitget	FILLED	dispatcher-bitget	2026-09-09 13:48:54.029833+00	1	historical-entry-filled-and-provider-flat-reconciled	2026-09-09 13:48:01.778903+00	2026-09-09 17:42:06.249691+00
1b14b580-8360-4797-a95a-d0015b0acd2f	canonical_signal	cde00049-479f-47a8-9090-9187fd2205ac	a405cd0f2ffd422e240616f5543e64ba03d5cfd9369896908db7c7be985766b9	bitget	REJECTED	dispatcher-bitget	2026-09-10 17:26:53.006589+00	1	Bitget account margin mode must be isolated	2026-09-10 17:25:57.567816+00	2026-09-10 17:26:24.372272+00
259b562d-7834-44db-8c6f-9cd2605b8c15	canonical_signal	3ec714d3-f171-565c-ae0f-8a676f7f4262	a888af4db7c17b1ab1776498824f297f1c2fe03c5dced816436dc4df23710259	bitget	REJECTED	dispatcher-bitget	2026-09-18 02:24:41.727647+00	1	Bitget GET /api/v2/mix/account/account HTTP 400: 40309 The symbol has been removed	2026-09-18 02:23:19.787173+00	2026-09-18 02:24:14.329792+00
9e9516b4-c2de-4bf2-954c-73d5a41f3310	canonical_signal	6bbee916-a466-4862-b865-195f4eb77b41	311a40ff51b8abb56a5cfd11ee6734814b5cb2b19ddc4b52322f8b2715dd2159	bitget	REJECTED	dispatcher-bitget	2026-09-13 03:49:03.784991+00	1	canary-order-cap-reached	2026-09-13 03:48:08.35757+00	2026-09-13 03:48:36.248357+00
7ecac60d-696d-4dcb-8fe3-34f3307ad596	canonical_signal	e49c5a39-cbe3-58b8-86b4-7f779f33d2e3	9e3b29dc3e0bcef60779c4152cd313676c69c065307fb474a9d9974a851bcea9	bitget	REJECTED	dispatcher-bitget	2026-09-15 16:18:12.33546+00	1	provider-flat-no-order-or-fill	2026-09-15 16:17:30.623636+00	2026-09-15 20:20:11.957717+00
23c8dc14-4553-454f-8eed-247da9e50b56	canonical_signal	f8f504e1-f15c-4625-be04-b529b3d810c5	81d987b81e65a91aa9abdc4726f6a09e83cac4cbe45514eec2baae970c3a1068	bitget	FILLED	dispatcher-bitget	2026-09-12 11:42:27.84552+00	1	entry-filled-fallback-protection-active	2026-09-12 11:41:19.952743+00	2026-09-12 14:38:48.731594+00
9c6d5119-4041-4951-af84-d9a2fceaeee6	canonical_signal	ad550c19-9fdb-5cad-9bc6-d3f33a78f397	a895d62692bfc5685b0294f2e8d1d5584c5228d3912d85d8ad5a0bb1c5d89187	bitget	REJECTED	dispatcher-bitget	2026-09-20 17:20:15.936459+00	1	Bitget GET /api/v2/mix/account/account HTTP 400: 40034 Parameter BONKUSDT does not exist	2026-09-20 17:19:35.304109+00	2026-09-20 17:19:46.632536+00
cb58a3cb-5c50-47f5-8228-306ebca6e813	canonical_signal	88967b6a-490a-5f40-bad6-33d0a5c669b1	4ad403a92954d6205b0c300e74fd0d67c5b371b7869f5c9a08a8de71910c4455	bitget	FILLED	dispatcher-bitget	2026-09-19 18:37:20.165236+00	1	fallback-protection-active	2026-09-19 18:36:41.625399+00	2026-09-19 18:36:54.901293+00
a12e4cfe-9e88-4406-8e18-6cce9549e1b7	canonical_signal	7ddd16f0-ff63-5a56-ad27-07df7e22a921	959de2ad5273b8f419be00253738cdcdf16498efb34cd8bbdd516a8f23209770	bitget	FILLED	dispatcher-bitget	2026-09-15 14:27:56.809112+00	1	fallback-protection-active	2026-09-15 14:09:27.432029+00	2026-09-15 14:27:31.508046+00
ff9a5f40-d9a3-40aa-984e-8628902c504b	canonical_signal	64574a60-2c3c-5c77-b931-d3456848e934	84f7c9437aaca5f320aa9c4f9ebd02870c82fdf3538d8572c0f0eb5e6b04634b	bitget	REJECTED	dispatcher-bitget	2026-09-17 20:26:09.656886+00	1	3 validation errors for VenueRiskConfig\nbase_margin_usdt\n  Input should be greater than 0 [type=greater_than, input_value=Decimal('0.00'), input_type=Decimal]\n    For further information visit https://errors.pydantic.dev/2.13/v/greater_than\nmax_auto_margin_usdt\n  Input should be greater than 0 [type=greater_than, input_value=Decimal('0.00'), input_type=Decimal]\n    For further information visit https://errors.pydantic.dev/2.13/v/greater_than\nmax_position_notional_usdt\n  Input should be greater than 0 [type=greater_than, input_value=Decimal('0.00'), input_type=Decimal]\n    For further information visit https://errors.pydantic.dev/2.13/v/greater_than	2026-09-17 20:25:04.654028+00	2026-09-17 20:25:42.420253+00
\.


--
-- Data for Name: fallback_protection; Type: TABLE DATA; Schema: public; Owner: fatty_app
--

COPY public.fallback_protection (id, exchange, symbol, direction, entry_price, stop_loss, take_profits, quantity, state, close_price, close_reason, close_order_id, created_at, updated_at, position_key) FROM stdin;
d01f3397-f563-4054-a681-6f5a9a251b69	bitget	ETHFIUSDT	LONG	0.7601	0.718	["0.87"]	6.8	cancelled	0.7091	position-already-flat-manual-close	fallback-sl_hit-ETHFIUSDT-1789251997	2026-09-12 14:38:48.731594+00	2026-09-12 22:28:02.902736+00	\N
1b625dc6-2c6b-4503-8f03-9f210a9ebb3d	bitget	EULUSDT	LONG	1.279	1.2365	["1.47"]	26.9	cancelled	\N	position-already-flat	\N	2026-09-12 17:31:40.158266+00	2026-09-13 14:39:37.669388+00	\N
59a7c662-89f2-4c19-a1cc-9e6e785cf11a	bitget	WLDUSDT	LONG	0.3971	0.388	["0.427"]	91	cancelled	\N	position-already-flat	\N	2026-09-13 14:28:35.288487+00	2026-09-13 19:37:22.104798+00	live-bitget-WLDUSDT-d47acac9271651b2
93dd7479-9093-4093-92e9-a6ba8957b593	bitget	SNDKUSDT	LONG	1557.61	1535	["1795"]	0.018	active	\N	\N	\N	2026-09-15 14:27:31.489313+00	2026-09-15 14:27:31.489313+00	live-bitget-SNDKUSDT-ef3d1b13d05c5824
45b09b74-f120-4458-bab1-ae045a3e564b	bitget	WLDUSDT	SHORT	0.3874	0.3938	[]	80	cancelled	\N	operator-close-provider-flat	\N	2026-09-14 22:42:32.333709+00	2026-09-15 14:32:46.745352+00	live-bitget-WLDUSDT-d1111f6ed5f4508a
48975df9-cfe9-422c-a9dc-eed2dafecbaf	bitget	WLDUSDT	LONG	0.4331	0.4195	["0.46"]	72	active	\N	\N	\N	2026-09-19 18:36:54.888091+00	2026-09-19 18:36:54.888091+00	live-bitget-WLDUSDT-3490b8b58cdc53b3
\.


--
-- Data for Name: fills; Type: TABLE DATA; Schema: public; Owner: fatty_app
--

COPY public.fills (id, exchange, client_order_id, provider_fill_id, symbol, price, quantity, fee, fee_ccy, realized_pnl, filled_at) FROM stdin;
27f98f0f-5acb-5d7a-998c-5817bc514bf0	bitget	live-bitget-WLDUSDT-d47acac9271651b2	1483037919708712960	WLDUSDT	0.3971	91	0.02168166	USDT	0	2026-09-13 14:28:34.247+00
1a4683ae-bfac-567c-9a41-611443ef947b	bitget	provider-bitget-ec118a2429b8e703bd335458	1483115597766737920	WLDUSDT	0.3841	91	0.02097366	USDT	-1.17999853	2026-09-13 19:37:14.139+00
0595833c-4586-5e50-969e-88e43b6cf413	bitget	provider-bitget-2018f9c6cd09ad399fd99176	1476230982409117698	ENAUSDT	0.14741	39	0.00344939	USDT	-0.19344	2026-08-25 19:40:13.928+00
1c9d8586-0d1d-50ba-8106-97891ca9b56a	bitget	provider-bitget-a451acf048bd6423ed8b966a	1475834704491712518	FARTCOINUSDT	0.1773	5.6	0.00059572	USDT	-0.03528	2026-08-24 17:25:33.91+00
1f7fdd28-2fea-5c4f-8e57-ca7fa9787b12	bitget	provider-bitget-f749f5f1e4a72aecabb3ea01	1475176827540209668	ORDIUSDT	4.006	1.41	0.00338907	USDT	-0.33276	2026-08-22 21:51:23.822+00
0fe217fd-99e5-559b-8462-328284a3b7b8	bitget	live-bitget-WLDUSDT-d1111f6ed5f4508a	1483524614854021120	WLDUSDT	0.3874	80	0.0185952	USDT	0	2026-09-14 22:42:31.412+00
bf2b4003-8a34-5adf-ae95-908e008d9869	bitget	live-bitget-SNDKUSDT-ef3d1b13d05c5824	1483762428061409280	SNDKUSDT	1557.61	0.018	0.01682218	USDT	0	2026-09-15 14:27:30.499+00
ab429fa5-0f04-5e3b-8e5b-610fd3429ebb	bitget	live-bitget-WLDUSDT-3490b8b58cdc53b3	1485274740337414144	WLDUSDT	0.4331	72	0.01870992	USDT	0	2026-09-19 18:36:53.843+00
\.


--
-- Data for Name: intent_reconciliation_audit; Type: TABLE DATA; Schema: public; Owner: fatty_app
--

COPY public.intent_reconciliation_audit (id, exchange, client_order_id, prior_state, resolved_state, provider_snapshot, approval_reference, reconciled_at) FROM stdin;
7c5ccac4-b42d-4b30-9854-775064aaf5e2	bitget	live-bitget-NOTUSDT-7136e226e8124862	requested	rejected	{"open_positions": 0, "pending_orders": 0}	hernanda-approved-live-20260908	2026-09-08 17:00:20.4316+00
4ce90eeb-1332-4557-b91b-88de349dcbe3	bitget	live-bitget-NOTUSDT-7136e226e8124862-1788839186-emergency	submitted	reconciled	{"open_positions": 0, "pending_orders": 0}	hernanda-approved-live-20260908	2026-09-08 17:00:23.512566+00
\.


--
-- Data for Name: live_order_intents; Type: TABLE DATA; Schema: public; Owner: fatty_app
--

COPY public.live_order_intents (id, exchange, client_order_id, provider_order_id, symbol, side, role, state, requested_qty, acknowledged_qty, filled_qty, requested_price, acknowledged_price, filled_price, leverage, margin_mode, created_at, updated_at, fee, provider_fill_ids) FROM stdin;
aa87178f-cd3a-5abe-9a63-3218212546d1	bitget	live-bitget-NOTUSDT-7136e226e8124862-1788839186	1481064390118227970	NOTUSDT	BUY	ENTRY	filled	10820	\N	10820	\N	\N	\N	\N	\N	2026-09-08 03:46:26.657734+00	2026-09-08 03:46:28.81373+00	0	[]
846c8d76-b704-5ccc-95d3-ba2ac49566ed	bitget	live-bitget-NOTUSDT-7136e226e8124862	\N	NOTUSDT	BUY	ENTRY	rejected	10820	\N	0	\N	\N	\N	\N	\N	2026-09-08 02:58:01.40898+00	2026-09-08 17:00:20.4316+00	0	[]
7b40e69c-e420-5bd3-b156-8b9a79f5db73	bitget	live-bitget-NOTUSDT-7136e226e8124862-1788839186-emergency	1481064394882957313	NOTUSDT	SELL	EMERGENCY_CLOSE	reconciled	10820	\N	0	\N	\N	\N	\N	\N	2026-09-08 03:46:29.124221+00	2026-09-08 17:00:23.512566+00	0	[]
4388dcaa-8cec-55bb-9ca5-00bbbb46a3ce	bitget	live-bitget-WLDUSDT-6072e55edd0c4b86	1481578277016059904	WLDUSDT	BUY	ENTRY	filled	12	\N	12	\N	\N	\N	\N	\N	2026-09-09 13:48:26.694645+00	2026-09-09 13:48:29.028959+00	0	[]
70e34e42-4547-5e81-a215-53f70d3a5c19	bitget	live-bitget-WLDUSDT-6072e55edd0c4b86-emergency	1481578281885646851	WLDUSDT	SELL	EMERGENCY_CLOSE	filled	12	\N	12	\N	\N	0.4506	\N	\N	2026-09-09 13:48:29.343043+00	2026-09-09 17:42:05.587298+00	-0.00324432	["1481578282081849344"]
5239f12b-ae01-588f-b8f4-f655b308fb2d	bitget	live-bitget-GRASSUSDT-c000000000000000	1482232100134993922	GRASSUSDT	BUY	ENTRY	filled	15	\N	15	\N	\N	\N	\N	\N	2026-09-11 09:06:30.143106+00	2026-09-11 09:06:32.582851+00	0	[]
421ff7d6-11fb-5d78-a44b-00bc15e49f3d	bitget	live-bitget-ETHFIUSDT-23c8dc144553454f	1482633621448310785	ETHFIUSDT	BUY	ENTRY	filled	6.8	\N	6.8	\N	\N	\N	\N	\N	2026-09-12 11:42:00.521841+00	2026-09-12 11:42:02.720902+00	0	[]
c6b16e60-175a-5c78-8495-c89ede3fd849	bitget	live-bitget-GRASSUSDT-e000000000000000	1482239522576678914	GRASSUSDT	BUY	ENTRY	reconciled	15	\N	0	\N	\N	\N	\N	\N	2026-09-11 09:40:34.98454+00	2026-09-11 10:21:15.889941+00	0	[]
e99749ac-589f-5d5c-9911-2968947afe92	bitget	live-bitget-EULUSDT-9ea05801e066409a	1482721605954715649	EULUSDT	BUY	ENTRY	filled	26.9	\N	26.9	\N	\N	\N	\N	\N	2026-09-12 17:31:37.657751+00	2026-09-12 17:31:39.859136+00	0	[]
cff5775e-6cf2-53b3-899e-3acf26357462	bitget	live-bitget-WLDUSDT-d47acac9271651b2	1483037919474761730	WLDUSDT	BUY	ENTRY	filled	91	\N	91	\N	\N	0.3971	\N	\N	2026-09-13 14:28:32.576378+00	2026-09-13 14:28:34.902557+00	0.02168166	["1483037919708712960"]
b543ef0f-66e0-5a83-9923-34fdf7085102	bitget	live-bitget-GRASSUSDT-c000000000000000-emergency	1482232104933277699	GRASSUSDT	SELL	EMERGENCY_CLOSE	filled	15	\N	15	\N	\N	\N	\N	\N	2026-09-11 09:06:32.895454+00	2026-09-11 16:16:29.836569+00	0	[]
602e40da-6c88-5265-8808-bb9750f08f40	bitget	live-bitget-INJUSDT-02de5cb48e114916	1482373439547916292	INJUSDT	BUY	ENTRY	filled	0.9	\N	0.9	\N	\N	\N	\N	\N	2026-09-11 18:28:08.343573+00	2026-09-11 18:28:10.518395+00	0	[]
7ef7c5d1-b8f8-5a89-85c8-77cad270dbd2	bitget	provider-bitget-ec118a2429b8e703bd335458	1483115597766737921	WLDUSDT	SELL	CLOSE	filled	91	\N	91	\N	\N	0.3841	\N	\N	2026-09-14 04:51:53.439642+00	2026-09-14 04:51:53.456208+00	0.02097366	["1483115597766737920"]
442f5d14-7398-5c40-9b75-fb0050404142	bitget	provider-bitget-2018f9c6cd09ad399fd99176	1476230982368116738	ENAUSDT	SELL	CLOSE	filled	39	\N	39	\N	\N	0.14741	\N	\N	2026-09-14 04:51:53.494496+00	2026-09-14 04:51:53.513019+00	0.00344939	["1476230982409117698"]
52a5718b-3c09-5ca9-93bc-ff175ffcf926	bitget	provider-bitget-a451acf048bd6423ed8b966a	1475834704450699268	FARTCOINUSDT	SELL	CLOSE	filled	5.6	\N	5.6	\N	\N	0.1773	\N	\N	2026-09-14 04:51:53.54716+00	2026-09-14 04:51:53.562942+00	0.00059572	["1475834704491712518"]
d3f1d77a-443f-5686-b9ac-ed1f2f14961c	bitget	provider-bitget-f749f5f1e4a72aecabb3ea01	1475176827503394818	ORDIUSDT	SELL	CLOSE	filled	1.41	\N	1.41	\N	\N	4.006	\N	\N	2026-09-14 04:51:53.59262+00	2026-09-14 04:51:53.609867+00	0.00338907	["1475176827540209668"]
a3c0ccfa-95d4-5cb2-a186-3a5ac643a8ab	bitget	live-bitget-WLDUSDT-d1111f6ed5f4508a	1483524614607486977	WLDUSDT	SELL	ENTRY	filled	80	\N	80	\N	\N	0.3874	\N	\N	2026-09-14 22:42:29.944507+00	2026-09-14 22:42:32.014515+00	0.0185952	["1483524614854021120"]
e49378e4-6234-5e94-be37-8ac7fe6d282c	bitget	live-bitget-SNDKUSDT-ef3d1b13d05c5824	1483762427819081730	SNDKUSDT	BUY	ENTRY	filled	0.018	\N	0.018	\N	\N	1557.61	\N	\N	2026-09-15 14:27:28.973924+00	2026-09-15 14:27:31.147686+00	0.01682218	["1483762428061409280"]
cbaeb0f7-21bc-53b4-afda-a66836f937d3	bitget	operator-close-2213def064eb4679b1ce963d4355c22c	1483763537761943553	WLDUSDT	BUY	CLOSE	reconciled	80	\N	0	\N	\N	\N	\N	\N	2026-09-15 14:31:54.940526+00	2026-09-15 14:31:55.512333+00	0	[]
ffe8c21e-5ebd-5beb-b226-8859769dd765	bitget	live-bitget-INJUSDT-02de5cb48e114916-emergency	1482373444140679169	INJUSDT	SELL	EMERGENCY_CLOSE	filled	0.9	\N	0.9	\N	\N	\N	\N	\N	2026-09-11 18:28:10.797385+00	2026-09-11 18:36:15.410231+00	0	[]
d52513a8-37cf-5522-b4db-ea48c59542dd	bitget	live-bitget-TAOUSDT-71295d5315595f73	\N	TAOUSDT	BUY	ENTRY	rejected	0.147	\N	0	\N	\N	\N	\N	\N	2026-09-15 16:17:44.395114+00	2026-09-15 20:20:11.957717+00	0	[]
1b41d1f0-b002-525d-ae9c-8e9b91eac1c9	bitget	live-bitget-WLDUSDT-3490b8b58cdc53b3	1485274740095074305	WLDUSDT	BUY	ENTRY	filled	72	\N	72	\N	\N	0.4331	\N	\N	2026-09-19 18:36:52.22551+00	2026-09-19 18:36:54.495675+00	0.01870992	["1485274740337414144"]
\.


--
-- Data for Name: notifications_outbox; Type: TABLE DATA; Schema: public; Owner: fatty_app
--

COPY public.notifications_outbox (id, dedup_key, payload, attempts, sent_at, created_at, claimed_by, lease_until, next_attempt_at, failed_at) FROM stdin;
a83ce1ef-45d9-48d6-b496-4227c200cc8c	dispatch-transition:7136e226-e812-4862-b009-7f0f0c688580:VALIDATED:REJECTED	{"kind": "execution-event", "reason": "canary-order-cap-reached", "to_state": "REJECTED", "from_state": "VALIDATED", "dispatch_id": "7136e226-e812-4862-b009-7f0f0c688580"}	1	2026-09-07 18:24:54.911132+00	2026-09-07 18:24:52.556584+00	\N	\N	\N	\N
46387d70-4c95-4452-970c-9000974af2e2	analysis:854d4686-8f42-48ef-9997-a174886c7718:3f8ed5920fbd14b5b6afd0c1137ebc2797aa5634d286747c6995e2e8b20f1e9d	{"kind": "signal-analysis", "pair": "WLD", "entry": "0.47", "status": "CODEX_SUCCEEDED", "direction": "LONG", "stop_loss": "0.4562", "dispatches": 2, "take_profits": ["0.51"], "failure_class": "none", "source_revision": "3f8ed5920fbd14b5b6afd0c1137ebc2797aa5634d286747c6995e2e8b20f1e9d", "canonical_signal": true, "source_message_id": 16103}	1	2026-09-08 14:06:21.322494+00	2026-09-08 14:06:08.021238+00	\N	\N	\N	\N
5c7b969e-a426-40e8-90c9-42c2ba0db02c	heartbeat:82816	{"host": "fspmi-hostinger", "kind": "heartbeat", "mode": "DEMO", "codex": "UNCONFIGURED", "failed": 0, "source": "@fattyfatclub", "analyzed": 8, "received": 0, "dispatches": 6, "venue_mode": "DEMO", "raw_messages": 8, "canonical_signals": 3, "execution_enabled": "0", "live_order_intents": 1, "notification_failed": 0, "notification_pending": 0, "latest_source_message_id": 16100, "latest_source_received_at": "2026-09-07 13:54:27+00:00"}	1	2026-09-08 01:55:03.460369+00	2026-09-08 01:54:57.137871+00	\N	\N	\N	\N
1184daf6-4a7d-4d9c-a2e6-b4b573457fa3	dispatch-transition:7136e226-e812-4862-b009-7f0f0c688580:SUBMITTING:ACKNOWLEDGED	{"kind": "execution-event", "reason": "none", "to_state": "ACKNOWLEDGED", "from_state": "SUBMITTING", "dispatch_id": "7136e226-e812-4862-b009-7f0f0c688580"}	1	2026-09-08 02:53:05.67838+00	2026-09-08 02:53:03.003737+00	\N	\N	\N	\N
64c9c6be-d382-40e2-ab75-ecafe6bc3205	autonomous-demo-cycle:735a2f55-bc92-4cb1-ac92-c3c9d37593b5	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "735a2f55-bc92-4cb1-ac92-c3c9d37593b5", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 02:03:01.690498+00	2026-09-07 02:02:58.725017+00	\N	\N	\N	\N
41a9e99b-fc2f-434e-8554-0a8ede151e6a	kill-switch:bitget:provider-orders-invalid	{"scope": "bitget", "reason": "provider-orders-invalid"}	1	2026-09-05 18:31:12.542459+00	2026-09-05 16:59:44.788365+00	\N	\N	\N	\N
8edca003-38bb-4c15-b005-4ffc8c6905b8	analysis:6ace015d-1783-4761-93b8-c7813b28cb4c:ce028c03b5b7d7e54c1c09d16b2e937624a13126cb1e95ba672fb0475d11a223	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "CODEX_SUCCEEDED", "direction": "none", "stop_loss": "none", "dispatches": 0, "take_profits": [], "failure_class": "none", "source_revision": "ce028c03b5b7d7e54c1c09d16b2e937624a13126cb1e95ba672fb0475d11a223", "canonical_signal": false, "source_message_id": 16106}	1	2026-09-08 15:43:58.013114+00	2026-09-08 15:43:37.618953+00	\N	\N	\N	\N
695f3be4-1e8b-47b1-ba42-27c2d55af61b	source-forward:-1001252615519:16102:72a1ea919d92a7d03c348f1bd0edae5b8f2db9e6ad4f760caf74799d82aedf12	{"kind": "source-forward", "raw_text": "Sl hit on $NOT", "has_media": false, "source_revision": "72a1ea919d92a7d03c348f1bd0edae5b8f2db9e6ad4f760caf74799d82aedf12", "source_channel_id": -1001252615519, "source_message_id": 16102}	1	2026-09-08 14:00:12.854428+00	2026-09-08 14:00:07.679545+00	\N	\N	\N	\N
fe889af4-ac19-4892-a9e8-ac86e44bae90	bitget-dispatch:09358d64-e2ba-4ef5-942e-3198d9476289:kill-switch-latched	{"kind": "execution-alert", "reason": "kill-switch-latched", "dispatch_id": "09358d64-e2ba-4ef5-942e-3198d9476289"}	1	2026-09-06 14:11:24.398656+00	2026-09-06 14:11:18.929638+00	\N	\N	\N	\N
2a68f074-15b6-489a-abd9-2ba6122b75c4	source-forward:-1001252615519:16105:b5aaad5a4824f06034aa26ddbbba4bf980573669cd337be77467ffa53d9f025f	{"kind": "source-forward", "raw_text": "$WLD TP1 booked here at 2R", "has_media": false, "source_revision": "b5aaad5a4824f06034aa26ddbbba4bf980573669cd337be77467ffa53d9f025f", "source_channel_id": -1001252615519, "source_message_id": 16105}	1	2026-09-08 15:43:37.082736+00	2026-09-08 15:43:33.278563+00	\N	\N	\N	\N
46bb67d2-2f9f-4893-9d19-8ada2baa76db	analysis:679b2eb6-3775-4146-be11-dbd14a862014:d29b9382cb8adef607fa002da0dfc9dbfa687f6187bc878acc5a027f1829f5cb	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "CODEX_SUCCEEDED", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "#XPL $XPL LONG TRADE\\n\\nENTRY: 0.098 - 0.096\\n\\nTARGET: 0.117\\n\\nStoploss: 0.09475", "take_profits": [], "failure_class": "none", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16108}	1	2026-09-08 20:48:32.06017+00	2026-09-08 20:48:20.886149+00	\N	\N	\N	\N
a262dca5-1393-437e-98a3-428c46b9a679	analysis:bcb72f30-0ed8-4e6d-9d11-e816243239f8:b5aaad5a4824f06034aa26ddbbba4bf980573669cd337be77467ffa53d9f025f	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "CODEX_SUCCEEDED", "direction": "none", "stop_loss": "none", "dispatches": 0, "take_profits": [], "failure_class": "none", "source_revision": "b5aaad5a4824f06034aa26ddbbba4bf980573669cd337be77467ffa53d9f025f", "canonical_signal": false, "source_message_id": 16105}	1	2026-09-08 15:43:58.313846+00	2026-09-08 15:43:37.618953+00	\N	\N	\N	\N
1666d453-01a5-43c0-898e-35bc2b635a42	dispatch-transition:9a7c19c1-c834-491d-9f15-b787b02fe0ab:QUEUED:REJECTED	{"kind": "execution-event", "reason": "cutover-gated", "to_state": "REJECTED", "from_state": "QUEUED", "dispatch_id": "9a7c19c1-c834-491d-9f15-b787b02fe0ab"}	1	2026-09-08 16:46:22.378459+00	2026-09-08 16:46:19.218185+00	\N	\N	\N	\N
ac01ce35-09d3-441a-a95d-8ad3b101338d	analysis:b9f2f84c-7384-4f27-bb09-95f8a5fa78e9:6bf6091c5994875245c5b4da64ef4a4618391a0b572db912ca2abe3f5b15e994	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "CODEX_SUCCEEDED", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "$BTC going perfect so far🔥\\n\\nhttps://x.com/learnernoearner/status/2097656935839383803?s=46", "take_profits": [], "failure_class": "none", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16110}	1	2026-09-09 12:04:15.99059+00	2026-09-09 12:03:57.415184+00	\N	\N	\N	\N
5b068d5a-de30-4b49-a1c9-baeeb84ca70c	dispatch-transition:6072e55e-dd0c-4b86-b716-04dbfcb31f6f:QUEUED:PREFLIGHT	{"kind": "execution-event", "reason": "none", "to_state": "PREFLIGHT", "from_state": "QUEUED", "dispatch_id": "6072e55e-dd0c-4b86-b716-04dbfcb31f6f"}	1	2026-09-09 13:48:25.274579+00	2026-09-09 13:48:24.155876+00	\N	\N	\N	\N
a0661555-9d4a-466e-9a4b-57512f1ea267	dispatch-transition:6072e55e-dd0c-4b86-b716-04dbfcb31f6f:SUBMITTING:UNKNOWN	{"kind": "execution-event", "reason": "provider-unknown", "to_state": "UNKNOWN", "from_state": "SUBMITTING", "dispatch_id": "6072e55e-dd0c-4b86-b716-04dbfcb31f6f"}	1	2026-09-09 13:48:31.636792+00	2026-09-09 13:48:29.640905+00	\N	\N	\N	\N
ebb76007-cc59-4530-aad0-f933576ad982	autonomous-demo-cycle:6e056a6c-845c-44fb-88b2-86fc6673007f	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "6e056a6c-845c-44fb-88b2-86fc6673007f", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 01:51:41.3806+00	2026-09-07 01:51:37.483241+00	\N	\N	\N	\N
9312d2e3-05c2-4393-9707-08b34f19a212	autonomous-demo-cycle:87430d9f-4c2f-4a91-a3c1-186427d04903	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "87430d9f-4c2f-4a91-a3c1-186427d04903", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 01:51:51.716878+00	2026-09-07 01:51:49.35352+00	\N	\N	\N	\N
407b6e04-0b2d-4283-86b1-9e6bed41fa8d	dispatch-transition:23c8dc14-4553-454f-8eed-247da9e50b56:QUEUED:PREFLIGHT	{"kind": "execution-event", "reason": "none", "to_state": "PREFLIGHT", "from_state": "QUEUED", "dispatch_id": "23c8dc14-4553-454f-8eed-247da9e50b56"}	1	2026-09-12 11:41:59.951326+00	2026-09-12 11:41:57.895493+00	\N	\N	\N	\N
f96dfa36-1c0b-4f40-8f67-bdef32e90439	bitget-dispatch:7136e226-e812-4862-b009-7f0f0c688580:canary-order-cap-reached	{"kind": "execution-alert", "reason": "canary-order-cap-reached", "dispatch_id": "7136e226-e812-4862-b009-7f0f0c688580"}	1	2026-09-07 18:24:55.312084+00	2026-09-07 18:24:52.582294+00	\N	\N	\N	\N
5863ab1b-a5f3-4eb5-bac2-1b5c031589c5	heartbeat:82817	{"host": "fspmi-hostinger", "kind": "heartbeat", "mode": "DEMO", "codex": "UNCONFIGURED", "failed": 0, "source": "@fattyfatclub", "analyzed": 8, "received": 0, "dispatches": 6, "venue_mode": "DEMO", "raw_messages": 8, "canonical_signals": 3, "execution_enabled": "0", "live_order_intents": 3, "notification_failed": 0, "notification_pending": 0, "latest_source_message_id": 16100, "latest_source_received_at": "2026-09-07 13:54:27+00:00"}	1	2026-09-08 07:55:00.248012+00	2026-09-08 07:54:57.457839+00	\N	\N	\N	\N
727530ca-2b78-46a0-98a2-664b85b9daad	dispatch-transition:7136e226-e812-4862-b009-7f0f0c688580:SUBMITTING:UNKNOWN	{"kind": "execution-event", "reason": "provider-unknown", "to_state": "UNKNOWN", "from_state": "SUBMITTING", "dispatch_id": "7136e226-e812-4862-b009-7f0f0c688580"}	6	2026-09-08 02:46:42.481341+00	2026-09-08 02:38:12.315281+00	\N	\N	2026-09-08 02:46:36.381272+00	\N
6f481d8a-7874-487b-bc57-e1352dbbad43	heartbeat:82811	{"host": "fspmi-hostinger", "kind": "heartbeat", "mode": "DEMO", "codex": "UNCONFIGURED", "failed": 0, "source": "@fattyfatclub", "analyzed": 6, "received": 0, "dispatches": 4, "venue_mode": "DEMO", "raw_messages": 6, "canonical_signals": 2, "execution_enabled": "1", "live_order_intents": 0, "notification_failed": 0, "notification_pending": 0, "latest_source_message_id": 16098, "latest_source_received_at": "2026-09-06 16:30:18+00:00"}	1	2026-09-06 23:55:55.910741+00	2026-09-06 23:55:54.104558+00	\N	\N	\N	\N
2eadef16-554c-4a99-9a31-690e50ac28fc	analysis:5776e5d7-ee6d-4f90-b65f-c666a131985a:72a1ea919d92a7d03c348f1bd0edae5b8f2db9e6ad4f760caf74799d82aedf12	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "CODEX_SUCCEEDED", "direction": "none", "stop_loss": "none", "dispatches": 0, "take_profits": [], "failure_class": "none", "source_revision": "72a1ea919d92a7d03c348f1bd0edae5b8f2db9e6ad4f760caf74799d82aedf12", "canonical_signal": false, "source_message_id": 16102}	1	2026-09-08 14:00:23.414098+00	2026-09-08 14:00:11.820142+00	\N	\N	\N	\N
9da8029a-d055-4b71-9471-4a68e1f7d056	analysis:56351963-d5fe-44b6-9ee7-02bcf16f0e81:d1b167e2d495786b90e2f3e117d73a5cded811658540d10aee84ec070f83f798	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "take_profits": [], "failure_class": "codex executable unavailable", "source_revision": "d1b167e2d495786b90e2f3e117d73a5cded811658540d10aee84ec070f83f798", "canonical_signal": false, "source_message_id": 16098}	1	2026-09-06 16:30:57.883039+00	2026-09-06 16:30:54.781455+00	\N	\N	\N	\N
08586d0a-c3ba-48b3-947c-951e78ad184d	analysis:5714ab4a-772b-420e-84b4-3759d8ccdac3:d6e75e6b2802d2b7425a2f6687d092b91c5a80c83c63531474003d33a9e5c9e2	{"kind": "signal-analysis", "pair": "SUSHI", "entry": "0.2512", "status": "FALLBACK_ACCEPTED", "direction": "LONG", "stop_loss": "0.2428", "dispatches": 2, "take_profits": ["0.2835"], "failure_class": "codex executable unavailable", "source_revision": "d6e75e6b2802d2b7425a2f6687d092b91c5a80c83c63531474003d33a9e5c9e2", "canonical_signal": true, "source_message_id": 16097}	1	2026-09-06 15:43:15.511077+00	2026-09-06 15:43:13.744644+00	\N	\N	\N	\N
9d27d51a-684e-48c5-a54c-6749ee18040e	dispatch-transition:577ed2b8-9511-4abc-851b-2f2d256714bf:QUEUED:REJECTED	{"kind": "execution-event", "reason": "cutover-gated", "to_state": "REJECTED", "from_state": "QUEUED", "dispatch_id": "577ed2b8-9511-4abc-851b-2f2d256714bf"}	1	2026-09-08 14:06:41.784106+00	2026-09-08 14:06:38.677353+00	\N	\N	\N	\N
55041160-0ad3-4184-a18c-536fec230a28	dispatch-transition:4a36d170-be56-4014-ae6b-8b4e86009e7d:QUEUED:REJECTED	{"kind": "execution-event", "reason": "kill-switch-latched", "to_state": "REJECTED", "from_state": "QUEUED", "dispatch_id": "4a36d170-be56-4014-ae6b-8b4e86009e7d"}	1	2026-09-06 15:43:45.954345+00	2026-09-06 15:43:42.961069+00	\N	\N	\N	\N
6046f823-a94d-4d41-9420-86e2aed02656	bitget-dispatch:4a36d170-be56-4014-ae6b-8b4e86009e7d:kill-switch-latched	{"kind": "execution-alert", "reason": "kill-switch-latched", "dispatch_id": "4a36d170-be56-4014-ae6b-8b4e86009e7d"}	1	2026-09-06 15:43:46.253892+00	2026-09-06 15:43:42.981319+00	\N	\N	\N	\N
e4844b94-602b-44af-a205-efc6c8ec06ee	kill-switch:bitget:unknown-intent-unreconciled:live-bitget-NOTUSDT-7136e226e8124862-1788839186-emergency	{"kind": "kill-switch", "scope": "bitget", "reason": "unknown-intent-unreconciled:live-bitget-NOTUSDT-7136e226e8124862-1788839186-emergency"}	1	2026-09-08 16:58:07.896511+00	2026-09-08 16:58:02.674704+00	\N	\N	\N	\N
ff0955ba-b8d3-4d51-92cd-03d569f03130	kill-switch-release:bitget:telegram-demo-authorization-20260906	{"kind": "kill-switch-release", "scope": "bitget", "approval_reference": "telegram-demo-authorization-20260906"}	1	2026-09-06 17:53:09.027488+00	2026-09-06 17:53:07.291764+00	\N	\N	\N	\N
7515e07a-8291-456b-9c54-16602774672d	heartbeat:82812	{"host": "fspmi-hostinger", "kind": "heartbeat", "mode": "DEMO", "codex": "UNCONFIGURED", "failed": 0, "source": "@fattyfatclub", "analyzed": 6, "received": 0, "dispatches": 4, "venue_mode": "DEMO", "raw_messages": 6, "canonical_signals": 2, "execution_enabled": "1", "live_order_intents": 0, "notification_failed": 0, "notification_pending": 0, "latest_source_message_id": 16098, "latest_source_received_at": "2026-09-06 16:30:18+00:00"}	1	2026-09-07 01:12:29.163502+00	2026-09-07 01:12:25.700463+00	\N	\N	\N	\N
10ee1987-9755-4a91-8836-1b9f5ca1e9be	dispatch-transition:4a36d170-be56-4014-ae6b-8b4e86009e7d:QUEUED:PREFLIGHT	{"kind": "execution-event", "reason": "none", "to_state": "PREFLIGHT", "from_state": "QUEUED", "dispatch_id": "4a36d170-be56-4014-ae6b-8b4e86009e7d"}	1	2026-09-07 01:12:54.834462+00	2026-09-07 01:12:53.157467+00	\N	\N	\N	\N
952ea5a1-2f49-44dd-8f79-92232f611443	dispatch-transition:09358d64-e2ba-4ef5-942e-3198d9476289:QUEUED:PREFLIGHT	{"kind": "execution-event", "reason": "none", "to_state": "PREFLIGHT", "from_state": "QUEUED", "dispatch_id": "09358d64-e2ba-4ef5-942e-3198d9476289"}	1	2026-09-07 01:12:54.561766+00	2026-09-07 01:12:51.448791+00	\N	\N	\N	\N
d2c35df3-c9e9-41c9-86b9-c360e2153a7d	system:bitget-auto-recovery-enabled	{"kind": "system-event", "message": "Auto-recovery enabled: kill switch replaced by alerting"}	1	2026-09-09 18:01:58.534078+00	2026-09-09 18:01:57.762563+00	\N	\N	\N	\N
20926c65-266a-49c1-8455-f01c63372028	dispatch-transition:09358d64-e2ba-4ef5-942e-3198d9476289:QUEUED:REJECTED	{"kind": "execution-event", "reason": "cutover-gated", "to_state": "REJECTED", "from_state": "QUEUED", "dispatch_id": "09358d64-e2ba-4ef5-942e-3198d9476289"}	1	2026-09-07 01:30:07.315642+00	2026-09-07 01:30:03.019158+00	\N	\N	\N	\N
89e92b5d-69f1-4a45-bf39-213364361c4d	bitget-dispatch:09358d64-e2ba-4ef5-942e-3198d9476289:cutover-gated	{"kind": "execution-alert", "reason": "cutover-gated", "dispatch_id": "09358d64-e2ba-4ef5-942e-3198d9476289"}	1	2026-09-07 01:30:07.672778+00	2026-09-07 01:30:03.069504+00	\N	\N	\N	\N
7ba5cb74-31b4-4e7f-bdc6-d3614a04549d	bitget-dispatch:4a36d170-be56-4014-ae6b-8b4e86009e7d:cutover-gated	{"kind": "execution-alert", "reason": "cutover-gated", "dispatch_id": "4a36d170-be56-4014-ae6b-8b4e86009e7d"}	1	2026-09-07 01:30:38.094015+00	2026-09-07 01:30:33.157081+00	\N	\N	\N	\N
58eb445e-9f51-4ec5-9c55-51f359a0a8fd	autonomous-demo-cycle:d613de0c-f148-4af1-9ae8-8f3bcbabc22a	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "d613de0c-f148-4af1-9ae8-8f3bcbabc22a", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 01:51:31.046797+00	2026-09-07 01:51:25.570326+00	\N	\N	\N	\N
31cf76fe-341d-498a-9211-4f8a96a2ff54	autonomous-demo-cycle:1164400f-3862-4fa8-bab9-bed1e72e88e7	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "1164400f-3862-4fa8-bab9-bed1e72e88e7", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 01:52:02.104332+00	2026-09-07 01:52:01.305827+00	\N	\N	\N	\N
63348e1a-5973-4e0c-8c52-f07dbc023954	autonomous-demo-cycle:687e5776-b35c-4084-a89f-ad2a4e7ffb18	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "687e5776-b35c-4084-a89f-ad2a4e7ffb18", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 01:54:59.541503+00	2026-09-07 01:54:55.684269+00	\N	\N	\N	\N
364991cd-d928-4175-b124-70bdb153d4fc	autonomous-demo-cycle:410fb183-4671-41aa-8db0-ddf994b21f32	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "410fb183-4671-41aa-8db0-ddf994b21f32", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 01:56:00.129985+00	2026-09-07 01:55:56.088395+00	\N	\N	\N	\N
9fd15f21-76ba-480a-8411-64fb1f065931	autonomous-demo-cycle:c144e38b-ddf6-4aae-bbc2-12bf2e826a85	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "c144e38b-ddf6-4aae-bbc2-12bf2e826a85", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 01:52:17.461465+00	2026-09-07 01:52:13.825433+00	\N	\N	\N	\N
6d5d6822-d3f8-49ce-b56d-05ebed1919c0	autonomous-demo-cycle:c7673536-08df-4845-8a08-1118258e4fdc	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "c7673536-08df-4845-8a08-1118258e4fdc", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 01:57:00.713856+00	2026-09-07 01:56:56.460721+00	\N	\N	\N	\N
2e557927-105b-46ba-bb4b-7e2c5c668c1d	autonomous-demo-cycle:ac9344d6-b518-4bc8-8e25-71e30b017ab0	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "ac9344d6-b518-4bc8-8e25-71e30b017ab0", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 01:53:29.535049+00	2026-09-07 01:53:27.080951+00	\N	\N	\N	\N
0b43330e-9d3a-4b78-8797-b1c299db94bc	autonomous-demo-cycle:ce0dd2fa-d3f5-4ded-a9f9-cd1d71046ddc	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "ce0dd2fa-d3f5-4ded-a9f9-cd1d71046ddc", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 01:52:27.781105+00	2026-09-07 01:52:25.63836+00	\N	\N	\N	\N
28c5c4aa-f47f-4eaf-b8e7-3512e3cefabf	autonomous-demo-cycle:5c127467-82c2-4169-baf3-9ea529a39815	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "5c127467-82c2-4169-baf3-9ea529a39815", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 01:58:01.343262+00	2026-09-07 01:57:56.857484+00	\N	\N	\N	\N
20a1df79-b197-420e-99b6-7e25cf386d82	autonomous-demo-cycle:211554c6-c40b-488c-ada9-a1c7dbf1fbbf	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "211554c6-c40b-488c-ada9-a1c7dbf1fbbf", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 01:59:01.954182+00	2026-09-07 01:58:57.216359+00	\N	\N	\N	\N
8a1bfbaa-9c5a-442e-ae87-004e53f27e2a	autonomous-demo-cycle:807b62f2-e456-4a5b-a0f0-ee27bf6aabd5	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "807b62f2-e456-4a5b-a0f0-ee27bf6aabd5", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 01:52:38.09512+00	2026-09-07 01:52:37.621098+00	\N	\N	\N	\N
f77b523f-1050-41eb-8234-04f3aafef122	autonomous-demo-cycle:796406a4-b6f0-4d54-8692-a07fded342ac	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "796406a4-b6f0-4d54-8692-a07fded342ac", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 02:00:03.775125+00	2026-09-07 01:59:57.57004+00	\N	\N	\N	\N
3c49f81e-f1e6-4826-92d8-2b8736d71661	autonomous-demo-cycle:591bb4e1-ebb6-4e33-ace3-34c048672ca7	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "591bb4e1-ebb6-4e33-ace3-34c048672ca7", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 01:54:20.895955+00	2026-09-07 01:54:16.053872+00	\N	\N	\N	\N
fe1b2bb0-ebaa-4395-9c47-09db582c9711	autonomous-demo-cycle:6fe05b8c-d21e-43ef-a279-dc55b35554a3	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "6fe05b8c-d21e-43ef-a279-dc55b35554a3", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 01:52:53.497398+00	2026-09-07 01:52:49.418079+00	\N	\N	\N	\N
5eb5e861-fca0-406b-a761-f68e34b2de32	autonomous-demo-cycle:92f2541f-e3b3-44d5-952c-0062371f6a5d	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "92f2541f-e3b3-44d5-952c-0062371f6a5d", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 02:01:00.353586+00	2026-09-07 02:00:57.950246+00	\N	\N	\N	\N
141d1f06-0507-45f5-8ce7-387b5ba76f53	autonomous-demo-cycle:80347a39-d07d-492a-b2cc-44cccbeabb33	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "80347a39-d07d-492a-b2cc-44cccbeabb33", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 01:53:39.87105+00	2026-09-07 01:53:39.027796+00	\N	\N	\N	\N
d6eef0b7-ca88-4091-8a38-583a7b35057f	autonomous-demo-cycle:deb15c25-5100-496c-a629-11ef93c0787c	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "deb15c25-5100-496c-a629-11ef93c0787c", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 01:53:03.833942+00	2026-09-07 01:53:02.456959+00	\N	\N	\N	\N
ae5df188-7e52-4d4f-843d-1189ad5c9330	autonomous-demo-cycle:c2f93d33-fbb1-4676-8424-9bd7f654e689	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "c2f93d33-fbb1-4676-8424-9bd7f654e689", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 02:02:01.011255+00	2026-09-07 02:01:58.329986+00	\N	\N	\N	\N
f53fedda-3e9a-4606-b4f7-6ffab9554d3d	autonomous-demo-cycle:d8f73fe7-c0b2-4a0d-b517-07bf5cb90cd6	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "d8f73fe7-c0b2-4a0d-b517-07bf5cb90cd6", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 01:53:19.18701+00	2026-09-07 01:53:15.349577+00	\N	\N	\N	\N
2e824a37-99e5-444c-b43c-3adaa0ab90c0	autonomous-demo-cycle:10355191-2c9e-4d49-a766-c5d9f5f957e7	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "10355191-2c9e-4d49-a766-c5d9f5f957e7", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 01:53:55.196816+00	2026-09-07 01:53:50.952316+00	\N	\N	\N	\N
4b1b5127-7f1a-4d59-9712-d0da0c4762e8	autonomous-demo-cycle:dd0a90a9-30b4-4a1e-9553-b7244a663b1d	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "dd0a90a9-30b4-4a1e-9553-b7244a663b1d", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 01:54:05.536205+00	2026-09-07 01:54:03.86825+00	\N	\N	\N	\N
d0dcb232-2d7c-4b34-912f-a04c9132ef2e	autonomous-demo-cycle:3ddbe8c6-0250-4808-acb1-7263e174cbc8	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "3ddbe8c6-0250-4808-acb1-7263e174cbc8", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 01:54:31.212934+00	2026-09-07 01:54:28.046455+00	\N	\N	\N	\N
32b7bcb2-e928-4736-986d-566b78b4279c	autonomous-demo-cycle:3e879b92-def9-44a4-9127-e3a796089d02	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "3e879b92-def9-44a4-9127-e3a796089d02", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 02:04:02.437268+00	2026-09-07 02:03:59.089844+00	\N	\N	\N	\N
36d44af8-ef62-4aca-b099-1e51da31e38d	autonomous-demo-cycle:52158986-0a83-41ed-a835-58b9604d7282	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "52158986-0a83-41ed-a835-58b9604d7282", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 02:05:03.21438+00	2026-09-07 02:04:59.494612+00	\N	\N	\N	\N
e0c2dbf0-0bb4-4c6b-9076-61ca36bc60e2	kill-switch:bitget:unknown-intent-unreconciled:live-bitget-NOTUSDT-7136e226e8124862	{"kind": "kill-switch", "scope": "bitget", "reason": "unknown-intent-unreconciled:live-bitget-NOTUSDT-7136e226e8124862"}	6	2026-09-08 02:46:57.760385+00	2026-09-08 02:38:13.93323+00	\N	\N	2026-09-08 02:46:51.455846+00	\N
cc7e367f-4463-44e8-b947-79b7fbb2f933	heartbeat:82813	{"host": "fspmi-hostinger", "kind": "heartbeat", "mode": "DEMO", "codex": "UNCONFIGURED", "failed": 0, "source": "@fattyfatclub", "analyzed": 6, "received": 0, "dispatches": 4, "venue_mode": "DEMO", "raw_messages": 6, "canonical_signals": 2, "execution_enabled": "0", "live_order_intents": 0, "notification_failed": 0, "notification_pending": 0, "latest_source_message_id": 16098, "latest_source_received_at": "2026-09-06 16:30:18+00:00"}	1	2026-09-07 07:54:59.239758+00	2026-09-07 07:54:56.082351+00	\N	\N	\N	\N
349bad3b-db7f-4e1c-ae04-b7f95558ee62	autonomous-demo-cycle:3972ca3f-fa76-4b42-826b-154aec67289d	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "3972ca3f-fa76-4b42-826b-154aec67289d", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 02:05:38.685621+00	2026-09-07 02:05:38.360095+00	\N	\N	\N	\N
c8a042ca-4f82-4770-b755-cd89e8e9e136	kill-switch-release:bitget:hernanda-approved-live-20260908	{"kind": "kill-switch-release", "scope": "bitget", "approval_reference": "hernanda-approved-live-20260908"}	1	2026-09-13 14:39:19.308131+00	2026-09-13 14:39:14.586188+00	\N	\N	\N	\N
e9cd1d55-a69e-40f7-8d30-d4622ae0633b	autonomous-demo-cycle:25e42613-14cb-49c3-9691-adc2b3eed91d	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "25e42613-14cb-49c3-9691-adc2b3eed91d", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 02:08:05.761723+00	2026-09-07 02:08:00.723829+00	\N	\N	\N	\N
c5ec8aa2-cb78-47c7-a1ac-eee9c1f630fe	autonomous-demo-cycle:b32e5dc8-192d-465e-aa55-d1d1c4e40b9a	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "b32e5dc8-192d-465e-aa55-d1d1c4e40b9a", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 02:06:04.075913+00	2026-09-07 02:05:59.896527+00	\N	\N	\N	\N
752c9cce-90bb-413f-b6b0-1e371ed376eb	autonomous-demo-cycle:4f8863a4-385d-489b-9838-9e62532c7c82	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "4f8863a4-385d-489b-9838-9e62532c7c82", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 02:06:39.54389+00	2026-09-07 02:06:38.762036+00	\N	\N	\N	\N
8a840e27-862f-489c-9909-860c4300c723	autonomous-demo-cycle:3739cb33-6355-45fb-ba4d-dd541d9fe087	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "3739cb33-6355-45fb-ba4d-dd541d9fe087", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 02:07:04.92704+00	2026-09-07 02:07:00.286121+00	\N	\N	\N	\N
d3073e6e-3c77-4864-bf48-025bfe449eef	autonomous-demo-cycle:2eb32cb8-24f4-4453-a996-67019ec5d0b2	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "2eb32cb8-24f4-4453-a996-67019ec5d0b2", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 02:08:41.202968+00	2026-09-07 02:08:39.504552+00	\N	\N	\N	\N
51ead3e4-2a8a-4da7-9d3f-0275638f2fb5	autonomous-demo-cycle:f8653b4c-7a04-4d94-8df9-f63f90d47fbc	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "f8653b4c-7a04-4d94-8df9-f63f90d47fbc", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 02:07:40.369163+00	2026-09-07 02:07:39.133204+00	\N	\N	\N	\N
caedffb8-a35e-4740-afeb-9b42c3bff5c9	autonomous-demo-cycle:fa360da8-6041-4628-9b6b-fdafcf05e79d	{"kind": "autonomous-demo-cycle", "mode": "DEMO", "cycle_id": "fa360da8-6041-4628-9b6b-fdafcf05e79d", "messages": 6, "actionable": 2, "dispatches": 4, "non_actionable": 4, "live_orders_sent": 0}	1	2026-09-07 02:09:06.598366+00	2026-09-07 02:09:01.710505+00	\N	\N	\N	\N
ba7e49a0-0ad3-4983-86f6-24f55e28c442	kill-switch:bitget:provider-positions-invalid	{"kind": "kill-switch", "scope": "bitget", "reason": "provider-positions-invalid"}	1	2026-09-07 12:28:15.591209+00	2026-09-07 12:28:11.626669+00	\N	\N	\N	\N
d4e19ab5-db1d-460c-aeb4-75cf989e2fc2	heartbeat:82814	{"host": "fspmi-hostinger", "kind": "heartbeat", "mode": "DEMO", "codex": "UNCONFIGURED", "failed": 0, "source": "@fattyfatclub", "analyzed": 7, "received": 0, "dispatches": 4, "venue_mode": "DEMO", "raw_messages": 7, "canonical_signals": 2, "execution_enabled": "0", "live_order_intents": 0, "notification_failed": 0, "notification_pending": 0, "latest_source_message_id": 16099, "latest_source_received_at": "2026-09-07 12:53:40+00:00"}	1	2026-09-07 13:54:59.36011+00	2026-09-07 13:54:56.515352+00	\N	\N	\N	\N
e0ecc63f-e4df-4da6-8c24-1233a6da5d30	analysis:d5ebf52d-a56b-4d9c-99a8-a00024969e85:1718d262b4c181aad6124e1df9d161b886f4c7740cb46a700baa6557b8a5c199	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "CODEX_SUCCEEDED", "direction": "none", "stop_loss": "none", "dispatches": 0, "take_profits": [], "failure_class": "none", "source_revision": "1718d262b4c181aad6124e1df9d161b886f4c7740cb46a700baa6557b8a5c199", "canonical_signal": false, "source_message_id": 16099}	1	2026-09-07 12:54:31.230443+00	2026-09-07 12:54:17.213972+00	\N	\N	\N	\N
c6f4c08e-b8c0-4a29-a373-ef23e0268682	analysis:a81098c7-eca3-4861-a937-824cf2ee8cf9:31ad0cd230efe4e6b0c18e274ea9a58ba6794f5ecad6d4cb89715908e7851ff6	{"kind": "signal-analysis", "pair": "NOT", "entry": "0.0004715", "status": "CODEX_SUCCEEDED", "direction": "LONG", "stop_loss": "0.000458", "dispatches": 2, "take_profits": ["0.00058"], "failure_class": "none", "source_revision": "31ad0cd230efe4e6b0c18e274ea9a58ba6794f5ecad6d4cb89715908e7851ff6", "canonical_signal": true, "source_message_id": 16100}	1	2026-09-07 13:55:14.909428+00	2026-09-07 13:55:03.479459+00	\N	\N	\N	\N
e27568cd-5750-44ad-a7ad-79b6f37cdac8	dispatch-transition:7136e226-e812-4862-b009-7f0f0c688580:QUEUED:REJECTED	{"kind": "execution-event", "reason": "kill-switch-latched", "to_state": "REJECTED", "from_state": "QUEUED", "dispatch_id": "7136e226-e812-4862-b009-7f0f0c688580"}	1	2026-09-07 13:55:40.403468+00	2026-09-07 13:55:38.179667+00	\N	\N	\N	\N
3421d79f-c471-4b5a-9b6f-85fd9a70b90e	bitget-dispatch:7136e226-e812-4862-b009-7f0f0c688580:kill-switch-latched	{"kind": "execution-alert", "reason": "kill-switch-latched", "dispatch_id": "7136e226-e812-4862-b009-7f0f0c688580"}	1	2026-09-07 13:55:40.690553+00	2026-09-07 13:55:38.211069+00	\N	\N	\N	\N
674ed433-3334-4535-b478-597b95da9b69	kill-switch-release:bitget:user-request-not-signal-audit-20260907	{"kind": "kill-switch-release", "scope": "bitget", "approval_reference": "user-request-not-signal-audit-20260907"}	1	2026-09-07 15:44:08.340993+00	2026-09-07 15:44:03.45887+00	\N	\N	\N	\N
048a774c-80d3-4ab4-b765-e21b7eedc3f9	heartbeat:82815	{"host": "fspmi-hostinger", "kind": "heartbeat", "mode": "DEMO", "codex": "UNCONFIGURED", "failed": 0, "source": "@fattyfatclub", "analyzed": 8, "received": 0, "dispatches": 6, "venue_mode": "DEMO", "raw_messages": 8, "canonical_signals": 3, "execution_enabled": "0", "live_order_intents": 1, "notification_failed": 0, "notification_pending": 0, "latest_source_message_id": 16100, "latest_source_received_at": "2026-09-07 13:54:27+00:00"}	1	2026-09-07 19:54:59.122902+00	2026-09-07 19:54:56.830037+00	\N	\N	\N	\N
aa915908-7dae-4ea1-9ca0-47905267f4ad	dispatch-transition:7136e226-e812-4862-b009-7f0f0c688580:QUEUED:PREFLIGHT	{"kind": "execution-event", "reason": "none", "to_state": "PREFLIGHT", "from_state": "QUEUED", "dispatch_id": "7136e226-e812-4862-b009-7f0f0c688580"}	1	2026-09-07 18:13:23.232505+00	2026-09-07 18:13:19.384147+00	\N	\N	\N	\N
fb03029a-addc-4b6b-b368-98d9cce8bd45	dispatch-transition:7136e226-e812-4862-b009-7f0f0c688580:PREFLIGHT:REJECTED	{"kind": "execution-event", "reason": "Bitget GET /api/v2/mix/account/account HTTP 400: 40034 Parameter NOTUSDT does not exist", "to_state": "REJECTED", "from_state": "PREFLIGHT", "dispatch_id": "7136e226-e812-4862-b009-7f0f0c688580"}	1	2026-09-07 18:13:23.692073+00	2026-09-07 18:13:19.809012+00	\N	\N	\N	\N
aa06451e-4028-47f6-8c5c-a552e138b92c	bitget-dispatch:7136e226-e812-4862-b009-7f0f0c688580:Bitget GET /api/v2/mix/account/account HTTP 400: 40034 Parameter NOTUSDT does not exist	{"kind": "execution-alert", "reason": "Bitget GET /api/v2/mix/account/account HTTP 400: 40034 Parameter NOTUSDT does not exist", "dispatch_id": "7136e226-e812-4862-b009-7f0f0c688580"}	1	2026-09-07 18:13:23.998085+00	2026-09-07 18:13:19.832312+00	\N	\N	\N	\N
7b11aab6-82ff-4273-a536-109973e2db3a	source-forward:-1001252615519:16103:3f8ed5920fbd14b5b6afd0c1137ebc2797aa5634d286747c6995e2e8b20f1e9d	{"kind": "source-forward", "raw_text": "#WLD $WLD LONG TRADE\\n\\nENTRY: 0.47\\n\\nTARGET: 0.51\\n\\nSTOPLOSS: 0.4562", "has_media": true, "source_revision": "3f8ed5920fbd14b5b6afd0c1137ebc2797aa5634d286747c6995e2e8b20f1e9d", "source_channel_id": -1001252615519, "source_message_id": 16103}	1	2026-09-08 14:06:10.830068+00	2026-09-08 14:06:07.877825+00	\N	\N	\N	\N
a6be92f1-4ff2-4ee9-b70d-b405bbace0ac	bitget-dispatch:7136e226-e812-4862-b009-7f0f0c688580:Bitget ticker response has invalid lastPr	{"kind": "execution-alert", "reason": "Bitget ticker response has invalid lastPr", "dispatch_id": "7136e226-e812-4862-b009-7f0f0c688580"}	1	2026-09-07 18:16:25.434875+00	2026-09-07 18:16:21.125077+00	\N	\N	\N	\N
a300f316-0c8b-4d40-91ba-23d574868a55	kill-switch:bitget:provider-fill-reconciliation-error	{"kind": "kill-switch", "scope": "bitget", "reason": "provider-fill-reconciliation-error"}	1	2026-09-14 04:48:21.654567+00	2026-09-14 04:48:17.741165+00	\N	\N	\N	\N
c273f4f7-8606-416f-b254-83e5a40932a1	bitget-dispatch:577ed2b8-9511-4abc-851b-2f2d256714bf:cutover-gated	{"kind": "execution-alert", "reason": "cutover-gated", "dispatch_id": "577ed2b8-9511-4abc-851b-2f2d256714bf"}	1	2026-09-08 14:06:42.088958+00	2026-09-08 14:06:38.727489+00	\N	\N	\N	\N
414a44cc-fe37-4043-9c26-d6dfb2e0e589	source-forward:-1001252615519:16106:ce028c03b5b7d7e54c1c09d16b2e937624a13126cb1e95ba672fb0475d11a223	{"kind": "source-forward", "raw_text": "$WLD TP1 booked here at 2R", "has_media": false, "source_revision": "ce028c03b5b7d7e54c1c09d16b2e937624a13126cb1e95ba672fb0475d11a223", "source_channel_id": -1001252615519, "source_message_id": 16106}	1	2026-09-08 15:43:37.467451+00	2026-09-08 15:43:33.310373+00	\N	\N	\N	\N
6c8df7a7-ecad-449a-9423-8a85bca68709	dispatch-transition:7136e226-e812-4862-b009-7f0f0c688580:PREFLIGHT:SIZED	{"kind": "execution-event", "reason": "none", "to_state": "SIZED", "from_state": "PREFLIGHT", "dispatch_id": "7136e226-e812-4862-b009-7f0f0c688580"}	1	2026-09-07 18:21:17.585708+00	2026-09-07 18:21:14.953494+00	\N	\N	\N	\N
f0856a7e-7d9c-4cf1-89cd-e5d9a8bf62fc	analysis:cd4f924e-26b5-44a5-9458-b55d98cd2fa5:16cf7ebc3bf041f9d21f79acbe944f89e8e089c3244dafc4ebbe43420e8e2f8f	{"kind": "signal-analysis", "pair": "FLOKI", "entry": "0.02665", "status": "CODEX_SUCCEEDED", "direction": "LONG", "stop_loss": "0.02587", "dispatches": 2, "source_text": "#FLOKI $FLOKI LONG TRADE\\n\\nENTRY: 0.02665\\n\\nTARGET: 0.0304\\n\\nSTOPLOSS: 0.02587", "take_profits": ["0.0304"], "failure_class": "none", "canonical_signal": true, "source_message_id": 16107}	1	2026-09-08 16:46:01.947433+00	2026-09-08 16:45:45.832345+00	\N	\N	\N	\N
d3c7d0e6-2030-4370-b7ec-ce639ec7fe7a	dispatch-transition:7136e226-e812-4862-b009-7f0f0c688580:SIZED:VALIDATED	{"kind": "execution-event", "reason": "none", "to_state": "VALIDATED", "from_state": "SIZED", "dispatch_id": "7136e226-e812-4862-b009-7f0f0c688580"}	1	2026-09-07 18:21:17.880643+00	2026-09-07 18:21:14.986612+00	\N	\N	\N	\N
5fc540c3-0c28-41e5-a7fa-6efb4e353f2c	dispatch-transition:7136e226-e812-4862-b009-7f0f0c688580:VALIDATED:SUBMITTING	{"kind": "execution-event", "reason": "none", "to_state": "SUBMITTING", "from_state": "VALIDATED", "dispatch_id": "7136e226-e812-4862-b009-7f0f0c688580"}	1	2026-09-07 18:21:18.157823+00	2026-09-07 18:21:15.060085+00	\N	\N	\N	\N
3f058d27-d1c1-41e1-bc97-6e2e22c9c530	kill-switch-release:bitget:hernanda-approved-live-20260908-historical-reconciled	{"kind": "kill-switch-release", "scope": "bitget", "approval_reference": "hernanda-approved-live-20260908-historical-reconciled"}	1	2026-09-08 17:01:10.285079+00	2026-09-08 17:01:05.311451+00	\N	\N	\N	\N
6655e204-b4d3-4d63-a791-ae84da085770	kill-switch:bitget:provider-read-failed	{"kind": "kill-switch", "scope": "bitget", "reason": "provider-read-failed"}	5	2026-09-08 02:48:23.688513+00	2026-09-08 02:42:23.844743+00	\N	\N	2026-09-08 02:48:17.108706+00	\N
c3161913-0399-4fb2-9d62-ab850e85ce85	analysis:d3b9a913-462f-447b-bc17-577624756abe:a306301908d23e20dad35d4c0fe9d7f490c8d50b3df60dc3854ef015adf1ce97	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "CODEX_SUCCEEDED", "direction": "none", "stop_loss": "none", "dispatches": 0, "take_profits": [], "failure_class": "none", "source_revision": "a306301908d23e20dad35d4c0fe9d7f490c8d50b3df60dc3854ef015adf1ce97", "canonical_signal": false, "source_message_id": 16101}	1	2026-09-08 13:35:32.755467+00	2026-09-08 13:35:15.375562+00	\N	\N	\N	\N
17111e7e-4052-495e-9ba9-f66220a86257	analysis:66ecc6fc-8612-45f2-adb4-c8f575d87681:16b5efa897d806eae7d25a9f4a501a66f9569c727bccb5f29b0e88d93b075373	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "CODEX_SUCCEEDED", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "$XPL sl to entry", "take_profits": [], "failure_class": "none", "canonical_signal": false, "management_action": "SL_TO_ENTRY", "management_symbol": "XPLUSDT", "source_message_id": 16109}	1	2026-09-08 22:57:44.026933+00	2026-09-08 22:57:30.550085+00	\N	\N	\N	\N
8fb32ca4-0277-4095-92f4-f04788347397	analysis:81fe0d12-d6d3-46d3-ac1b-83609ad149e6:e94dec6e9971b34e9b5566a5b16e783d418fbd2fbfe0853ff2ab6c817092212a	{"kind": "signal-analysis", "pair": "WLD", "entry": "0.4495", "status": "CODEX_SUCCEEDED", "direction": "LONG", "stop_loss": "0.4345", "dispatches": 2, "source_text": "#WLD $WLD LONG TRADE\\n\\nENTRY: 0.4495\\n\\nTARGET: 0.491\\n\\nSTOPLOSS: 0.4345", "take_profits": ["0.491"], "failure_class": "none", "canonical_signal": true, "management_action": null, "management_symbol": null, "source_message_id": 16111}	1	2026-09-09 13:48:19.79435+00	2026-09-09 13:48:01.778903+00	\N	\N	\N	\N
00def9b2-d292-4c08-9f9b-e9013bd2c3e0	dispatch-transition:6072e55e-dd0c-4b86-b716-04dbfcb31f6f:PREFLIGHT:SIZED	{"kind": "execution-event", "reason": "none", "to_state": "SIZED", "from_state": "PREFLIGHT", "dispatch_id": "6072e55e-dd0c-4b86-b716-04dbfcb31f6f"}	1	2026-09-09 13:48:30.60778+00	2026-09-09 13:48:26.548522+00	\N	\N	\N	\N
6242c6a6-38c6-41f1-878d-561212ce2add	dispatch-transition:6072e55e-dd0c-4b86-b716-04dbfcb31f6f:SIZED:VALIDATED	{"kind": "execution-event", "reason": "none", "to_state": "VALIDATED", "from_state": "SIZED", "dispatch_id": "6072e55e-dd0c-4b86-b716-04dbfcb31f6f"}	1	2026-09-09 13:48:30.898144+00	2026-09-09 13:48:26.594699+00	\N	\N	\N	\N
76b7d0f5-2321-4f17-a172-746781233789	dispatch-transition:6072e55e-dd0c-4b86-b716-04dbfcb31f6f:VALIDATED:SUBMITTING	{"kind": "execution-event", "reason": "none", "to_state": "SUBMITTING", "from_state": "VALIDATED", "dispatch_id": "6072e55e-dd0c-4b86-b716-04dbfcb31f6f"}	1	2026-09-09 13:48:31.254613+00	2026-09-09 13:48:26.645775+00	\N	\N	\N	\N
83b7cd3f-8a1a-4bc4-9e0d-4ed323e04517	kill-switch:bitget:unknown-intent-unreconciled:live-bitget-WLDUSDT-6072e55edd0c4b86-emergency	{"kind": "kill-switch", "scope": "bitget", "reason": "unknown-intent-unreconciled:live-bitget-WLDUSDT-6072e55edd0c4b86-emergency"}	1	2026-09-09 13:48:37.072547+00	2026-09-09 13:48:31.932391+00	\N	\N	\N	\N
02d2cfc7-f7f0-4f2c-bd65-b2805010b0fe	analysis:b2d7e19b-590d-4c69-b16f-b75c6e1d8568:320d24db49ec6d9d3ad2c764535ebc4425aa5c1c8b65b2b0b58e7c4d3d197961	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "$INJ sl to entry", "take_profits": [], "failure_class": "Only an instruction to move the stop loss to entry is present; side, entry price, stop-loss price, and take-profit are absent.", "canonical_signal": false, "management_action": "SL_TO_ENTRY", "management_symbol": "INJUSDT", "source_message_id": 16121, "source_received_at": "2026-09-11T20:07:43+00:00"}	1	2026-09-11 20:08:01.864497+00	2026-09-11 20:07:49.758675+00	\N	\N	\N	\N
ebe89b7d-2bad-40b8-a795-ee68f599413f	bitget-dispatch:1b14b580-8360-4797-a95a-d0015b0acd2f:Bitget account margin mode must be isolated	{"kind": "execution-alert", "reason": "Bitget account margin mode must be isolated", "dispatch_id": "1b14b580-8360-4797-a95a-d0015b0acd2f"}	1	2026-09-10 17:26:29.051752+00	2026-09-10 17:26:24.389495+00	\N	\N	\N	\N
060703e3-45a4-47fc-9b15-44c7099b5c60	analysis:a27542cb-42bd-4f7e-88ab-843b5e11f4c2:f13d0e71e5d9b99299fb1352c0ce179e1eec41d27612f2e1212b7b828c17ce7c	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "CODEX_SUCCEEDED", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "$FLOKI and $wld sl smashed", "take_profits": [], "failure_class": "none", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16112}	1	2026-09-09 17:46:26.478546+00	2026-09-09 17:46:15.442294+00	\N	\N	\N	\N
281a2292-5c4a-44a9-9bf8-f443d9d324cc	analysis:93031ac9-89d0-4107-8685-0395dafc9d64:a405cd0f2ffd422e240616f5543e64ba03d5cfd9369896908db7c7be985766b9	{"kind": "signal-analysis", "pair": "GRASS", "entry": "0.3505", "status": "CODEX_SUCCEEDED", "direction": "LONG", "stop_loss": "0.3382", "dispatches": 2, "source_text": "#GRASS $GRASS LONG TRADE\\n\\nENTRY: 0.3505\\n\\nTARGET: 0.414\\n\\nSTOPLOSS: 0.3382", "take_profits": ["0.414"], "failure_class": "none", "canonical_signal": true, "management_action": null, "management_symbol": null, "source_message_id": 16115}	1	2026-09-10 17:26:12.989221+00	2026-09-10 17:25:57.567816+00	\N	\N	\N	\N
fd3a009a-090a-42e9-b344-6ee1fef313e4	analysis:bd21219f-6703-40ab-b0c9-b3bbaeee97a7:0a8ce390cef3a536a447e1d4b1c05078df18c7e4eae83bfedac3577b066f0997	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "Goodmorning ❤️\\n\\nWaiting for the perfect opportunity to reveal itself", "take_profits": [], "failure_class": "No explicit trade signal, pair, side, entry, stop loss, or take profit is present.", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16113}	1	2026-09-10 12:59:41.437756+00	2026-09-10 12:59:29.603762+00	\N	\N	\N	\N
1776c2b2-7f1f-4030-a6e4-99108753e29b	dispatch-transition:23c8dc14-4553-454f-8eed-247da9e50b56:PREFLIGHT:SIZED	{"kind": "execution-event", "reason": "none", "to_state": "SIZED", "from_state": "PREFLIGHT", "dispatch_id": "23c8dc14-4553-454f-8eed-247da9e50b56"}	1	2026-09-12 11:42:05.495578+00	2026-09-12 11:42:00.424831+00	\N	\N	\N	\N
1979a5cc-9d55-4b13-95af-b8d89cb51992	dispatch-transition:23c8dc14-4553-454f-8eed-247da9e50b56:SIZED:VALIDATED	{"kind": "execution-event", "reason": "none", "to_state": "VALIDATED", "from_state": "SIZED", "dispatch_id": "23c8dc14-4553-454f-8eed-247da9e50b56"}	1	2026-09-12 11:42:05.813044+00	2026-09-12 11:42:00.443481+00	\N	\N	\N	\N
f9c8736c-e739-411a-a134-002d3ea59bd6	analysis:b797b455-8532-4b9a-abba-e84361a3b4f0:b5f80844c2d2e2408d21580f07be8c4d8de0a227ce7b99232fcb7f81e901283f	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "$BTC LTF\\n\\nWe are back to the range lows here, i would like another small drop to around 76 - 76.5k before looking for longs", "take_profits": [], "failure_class": "Long bias is mentioned, but stop loss and take-profit levels are absent; 76–76.5k is described as a potential drop before considering longs, not an explicit complete entry.", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16114}	1	2026-09-10 14:07:06.302235+00	2026-09-10 14:06:51.049127+00	\N	\N	\N	\N
cf2375a2-ce3c-4e17-8f2a-af96337654d5	dispatch-transition:1b14b580-8360-4797-a95a-d0015b0acd2f:QUEUED:PREFLIGHT	{"kind": "execution-event", "reason": "none", "to_state": "PREFLIGHT", "from_state": "QUEUED", "dispatch_id": "1b14b580-8360-4797-a95a-d0015b0acd2f"}	1	2026-09-10 17:26:28.382752+00	2026-09-10 17:26:23.075736+00	\N	\N	\N	\N
a99fc939-f84f-466d-b951-1528d8e17072	dispatch-transition:1b14b580-8360-4797-a95a-d0015b0acd2f:PREFLIGHT:REJECTED	{"kind": "execution-event", "reason": "Bitget account margin mode must be isolated", "to_state": "REJECTED", "from_state": "PREFLIGHT", "dispatch_id": "1b14b580-8360-4797-a95a-d0015b0acd2f"}	1	2026-09-10 17:26:28.715842+00	2026-09-10 17:26:24.372272+00	\N	\N	\N	\N
282fc464-b1e3-435e-911d-8ebcfbe0de01	dispatch-transition:c0000000-0000-0000-0000-000000000001:QUEUED:PREFLIGHT	{"kind": "execution-event", "reason": "none", "to_state": "PREFLIGHT", "from_state": "QUEUED", "dispatch_id": "c0000000-0000-0000-0000-000000000001"}	1	2026-09-11 09:03:30.554538+00	2026-09-11 09:03:26.384919+00	\N	\N	\N	\N
a4811c8c-8dd0-4fe4-96f8-bf1873524d30	analysis:a0000000-0000-0000-0000-000000000001:0                                                               	{"kind": "signal-analysis", "pair": "GRASS", "entry": "0.3469", "status": "CODEX_SUCCEEDED", "direction": "LONG", "stop_loss": "0.344", "dispatches": 2, "source_text": "MANUAL SIGNAL: GRASSUSDT LONG @ 0.34690 SL 0.34400 TP1 0.34960 TP2 0.35290", "take_profits": ["0.3496", "0.3529"], "failure_class": "none", "canonical_signal": true, "management_action": null, "management_symbol": null, "source_message_id": 999999999, "source_received_at": "2026-09-11T09:03:08.342879+00:00"}	1	2026-09-11 09:03:25.151817+00	2026-09-11 09:03:08.967412+00	\N	\N	\N	\N
10160f8d-f2d6-4032-ad13-cb626e647aa1	dispatch-transition:c0000000-0000-0000-0000-000000000001:SIZED:VALIDATED	{"kind": "execution-event", "reason": "none", "to_state": "VALIDATED", "from_state": "SIZED", "dispatch_id": "c0000000-0000-0000-0000-000000000001"}	1	2026-09-11 09:03:31.189278+00	2026-09-11 09:03:28.291287+00	\N	\N	\N	\N
78a95139-5fa5-4679-808a-2b07980cbe55	dispatch-transition:c0000000-0000-0000-0000-000000000001:PREFLIGHT:SIZED	{"kind": "execution-event", "reason": "none", "to_state": "SIZED", "from_state": "PREFLIGHT", "dispatch_id": "c0000000-0000-0000-0000-000000000001"}	1	2026-09-11 09:03:30.830264+00	2026-09-11 09:03:28.274348+00	\N	\N	\N	\N
c8616ee7-261f-4fc8-9ce9-5df8e558ade9	dispatch-transition:c0000000-0000-0000-0000-000000000001:VALIDATED:REJECTED	{"kind": "execution-event", "reason": "canary-order-cap-reached", "to_state": "REJECTED", "from_state": "VALIDATED", "dispatch_id": "c0000000-0000-0000-0000-000000000001"}	1	2026-09-11 09:03:31.463383+00	2026-09-11 09:03:28.327606+00	\N	\N	\N	\N
7d1906f3-63e9-426e-8fde-45ab576d9605	dispatch-transition:e0000000-0000-0000-0000-000000000001:SUBMITTING:UNKNOWN	{"kind": "execution-event", "reason": "provider-unknown", "to_state": "UNKNOWN", "from_state": "SUBMITTING", "dispatch_id": "e0000000-0000-0000-0000-000000000001"}	1	2026-09-11 09:36:04.654756+00	2026-09-11 09:36:02.856858+00	\N	\N	\N	\N
9c84890a-34fe-453c-928a-5966a6d6a86a	bitget-dispatch:c0000000-0000-0000-0000-000000000001:canary-order-cap-reached	{"kind": "execution-alert", "reason": "canary-order-cap-reached", "dispatch_id": "c0000000-0000-0000-0000-000000000001"}	1	2026-09-11 09:03:31.733093+00	2026-09-11 09:03:28.348312+00	\N	\N	\N	\N
ecb91333-2a6c-4873-aa92-b05cbe9e73a7	dispatch-transition:c0000000-0000-0000-0000-000000000001:VALIDATED:SUBMITTING	{"kind": "execution-event", "reason": "none", "to_state": "SUBMITTING", "from_state": "VALIDATED", "dispatch_id": "c0000000-0000-0000-0000-000000000001"}	1	2026-09-11 09:06:33.011545+00	2026-09-11 09:06:30.102134+00	\N	\N	\N	\N
e6984de7-4ccb-4d2b-aa8a-151cbddf5287	bitget-dispatch:9e9516b4-c2de-4bf2-954c-73d5a41f3310:canary-order-cap-reached	{"kind": "execution-alert", "reason": "canary-order-cap-reached", "dispatch_id": "9e9516b4-c2de-4bf2-954c-73d5a41f3310"}	1	2026-09-13 03:48:41.104817+00	2026-09-13 03:48:36.263166+00	\N	\N	\N	\N
ad34bd1b-1b98-4be3-96a6-cd8fed56eca4	dispatch-transition:c0000000-0000-0000-0000-000000000001:SUBMITTING:UNKNOWN	{"kind": "execution-event", "reason": "provider-unknown", "to_state": "UNKNOWN", "from_state": "SUBMITTING", "dispatch_id": "c0000000-0000-0000-0000-000000000001"}	1	2026-09-11 09:06:38.327832+00	2026-09-11 09:06:33.191757+00	\N	\N	\N	\N
31e2a21f-7298-41e4-bcee-0cbffe589b57	analysis:c1fbe501-d8d1-44b3-bc33-cc6dba866a0c:57c2b7299c3b2e931a8330d93104c091f6875b69e1dca37b03837004c3737fdc	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "goodmorning❤️\\n\\n $BTC hit our long area and pumped as expected", "take_profits": [], "failure_class": "BTC and long direction are mentioned, but no explicit entry, stop-loss, or take-profit prices are provided.", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16116, "source_received_at": "2026-09-11T11:17:32+00:00"}	1	2026-09-11 11:17:49.305297+00	2026-09-11 11:17:34.567068+00	\N	\N	\N	\N
8f063eb5-3b72-4aae-9db3-3b092eb0c19d	dispatch-transition:e0000000-0000-0000-0000-000000000001:QUEUED:PREFLIGHT	{"kind": "execution-event", "reason": "none", "to_state": "PREFLIGHT", "from_state": "QUEUED", "dispatch_id": "e0000000-0000-0000-0000-000000000001"}	1	2026-09-11 09:36:03.233238+00	2026-09-11 09:35:58.462031+00	\N	\N	\N	\N
f141b87e-cb8d-4c03-a697-b5be74c11a4d	dispatch-transition:e0000000-0000-0000-0000-000000000001:PREFLIGHT:SIZED	{"kind": "execution-event", "reason": "none", "to_state": "SIZED", "from_state": "PREFLIGHT", "dispatch_id": "e0000000-0000-0000-0000-000000000001"}	1	2026-09-11 09:36:03.597657+00	2026-09-11 09:35:59.970734+00	\N	\N	\N	\N
f3ba8c67-d90d-4d88-a479-2c8f3e4fdce2	dispatch-transition:e0000000-0000-0000-0000-000000000001:SIZED:VALIDATED	{"kind": "execution-event", "reason": "none", "to_state": "VALIDATED", "from_state": "SIZED", "dispatch_id": "e0000000-0000-0000-0000-000000000001"}	1	2026-09-11 09:36:03.931744+00	2026-09-11 09:35:59.986342+00	\N	\N	\N	\N
92d29476-1370-444d-bdb8-bbd5da59ab0d	analysis:2b4c7fc2-35f3-433c-bcac-0a1f105247e0:cd58651da321746e9036a1b1317164a1ed339d898b835ef760edca84f0797ab9	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "$BTC fully done", "take_profits": [], "failure_class": "Text only states \\"$BTC fully done\\" and contains no explicit side, entry, stop-loss, or take-profit prices.", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16117, "source_received_at": "2026-09-11T13:00:33+00:00"}	1	2026-09-11 13:01:46.401352+00	2026-09-11 13:01:32.537786+00	\N	\N	\N	\N
ab070a75-89d2-4d75-9d8b-60afe4ad326d	dispatch-transition:e0000000-0000-0000-0000-000000000001:VALIDATED:SUBMITTING	{"kind": "execution-event", "reason": "none", "to_state": "SUBMITTING", "from_state": "VALIDATED", "dispatch_id": "e0000000-0000-0000-0000-000000000001"}	1	2026-09-11 09:36:04.287871+00	2026-09-11 09:36:00.025595+00	\N	\N	\N	\N
b2b733d4-4e19-4fa1-961f-3b90b74a6f8e	analysis:0e1d31b9-fa8a-42c5-bf9c-d89d6924bfe7:473ee794a1674e10821d90b6a5fc8fff2d0e7e8070c750d78c5c6870e189c4bf	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "$ETH done", "take_profits": [], "failure_class": "Text only states \\"$ETH done\\"; no side, entry, stop loss, or take-profit is provided.", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16119, "source_received_at": "2026-09-11T14:18:52+00:00"}	1	2026-09-11 14:19:57.581975+00	2026-09-11 14:19:39.419125+00	\N	\N	\N	\N
a6151aca-3491-4ece-9208-8631bd6f9480	analysis:5a8f02af-6c93-412c-8121-b0010d83ae3f:c2fa10dd2c1f3c4951fb6c5cf9d692e24991b867228117591cc44ee86de2b3e3	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "$GRASS invalid", "take_profits": [], "failure_class": "Text explicitly marks GRASS as invalid and contains no trade parameters.", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16118, "source_received_at": "2026-09-11T14:18:40+00:00"}	1	2026-09-11 14:19:57.29357+00	2026-09-11 14:19:39.419125+00	\N	\N	\N	\N
93b0cb49-4e4a-49a6-829d-2504f0894713	analysis:966d30d5-26fd-48c7-adb9-ba8dabffdbe3:4dd1c1b34fe2310ae1fb634d8cd6a07e1999a5e4312c4c071b5451197004a418	{"kind": "signal-analysis", "pair": "INJ", "entry": "5.835", "status": "CODEX_SUCCEEDED", "direction": "LONG", "stop_loss": "5.66", "dispatches": 2, "source_text": "#INJ $INJ LONG TRADE\\n\\nENTRY: 5.835\\n\\nTARGET: 6.72\\n\\nSTOPLOSS: 5.66", "take_profits": ["6.72"], "failure_class": "none", "canonical_signal": true, "management_action": null, "management_symbol": null, "source_message_id": 16120, "source_received_at": "2026-09-11T18:27:28+00:00"}	1	2026-09-11 18:27:56.845066+00	2026-09-11 18:27:44.546318+00	\N	\N	\N	\N
c10d11ce-efa9-4b3f-99eb-4416981a11e9	dispatch-transition:02de5cb4-8e11-4916-929b-6d95c1b51b1f:QUEUED:PREFLIGHT	{"kind": "execution-event", "reason": "none", "to_state": "PREFLIGHT", "from_state": "QUEUED", "dispatch_id": "02de5cb4-8e11-4916-929b-6d95c1b51b1f"}	1	2026-09-11 18:28:07.217018+00	2026-09-11 18:28:06.210148+00	\N	\N	\N	\N
f4bbcd33-e1cd-40f3-a5dd-2bb0496ae088	dispatch-transition:02de5cb4-8e11-4916-929b-6d95c1b51b1f:PREFLIGHT:SIZED	{"kind": "execution-event", "reason": "none", "to_state": "SIZED", "from_state": "PREFLIGHT", "dispatch_id": "02de5cb4-8e11-4916-929b-6d95c1b51b1f"}	1	2026-09-11 18:28:12.551527+00	2026-09-11 18:28:08.245709+00	\N	\N	\N	\N
ff9cb35e-1d9a-449e-863f-0bf72f23d068	dispatch-transition:02de5cb4-8e11-4916-929b-6d95c1b51b1f:SIZED:VALIDATED	{"kind": "execution-event", "reason": "none", "to_state": "VALIDATED", "from_state": "SIZED", "dispatch_id": "02de5cb4-8e11-4916-929b-6d95c1b51b1f"}	1	2026-09-11 18:28:12.881073+00	2026-09-11 18:28:08.264103+00	\N	\N	\N	\N
5fcde54e-49aa-4ea9-bba2-63ee92ebcec0	dispatch-transition:02de5cb4-8e11-4916-929b-6d95c1b51b1f:VALIDATED:SUBMITTING	{"kind": "execution-event", "reason": "none", "to_state": "SUBMITTING", "from_state": "VALIDATED", "dispatch_id": "02de5cb4-8e11-4916-929b-6d95c1b51b1f"}	1	2026-09-11 18:28:13.190269+00	2026-09-11 18:28:08.306725+00	\N	\N	\N	\N
5e3825de-5a17-4e64-abdf-26512679c0b2	dispatch-transition:02de5cb4-8e11-4916-929b-6d95c1b51b1f:SUBMITTING:UNKNOWN	{"kind": "execution-event", "reason": "provider-unknown", "to_state": "UNKNOWN", "from_state": "SUBMITTING", "dispatch_id": "02de5cb4-8e11-4916-929b-6d95c1b51b1f"}	1	2026-09-11 18:28:13.46463+00	2026-09-11 18:28:11.077588+00	\N	\N	\N	\N
a5f47c78-bfaa-4354-8670-f421f014bb82	analysis:561dc43c-ecea-4701-8276-633ea8d47f00:81d987b81e65a91aa9abdc4726f6a09e83cac4cbe45514eec2baae970c3a1068	{"kind": "signal-analysis", "pair": "ETHFI", "entry": "0.751", "status": "FALLBACK_ACCEPTED", "direction": "LONG", "stop_loss": "0.718", "dispatches": 2, "source_text": "#ETHFI $ETHFI LONG TRADE\\n\\nENTRY: 0.751 - 0.73\\n\\nTARGET: 0.87\\n\\nSTOPLOSS: 0.718", "take_profits": ["0.87"], "failure_class": "invalid classifier JSON: [<class 'decimal.ConversionSyntax'>]", "canonical_signal": true, "management_action": null, "management_symbol": null, "source_message_id": 16122, "source_received_at": "2026-09-12T11:40:47+00:00"}	1	2026-09-12 11:41:34.52056+00	2026-09-12 11:41:19.952743+00	\N	\N	\N	\N
dd894bdd-1f73-49b1-9357-d16000e5d3c6	dispatch-transition:ff9a5f40-d9a3-40aa-984e-8628902c504b:PREFLIGHT:REJECTED	{"kind": "execution-event", "reason": "3 validation errors for VenueRiskConfig\\nbase_margin_usdt\\n  Input should be greater than 0 [type=greater_than, input_value=Decimal('0.00'), input_type=Decimal]\\n    For further information visit https://errors.pydantic.dev/2.13/v/greater_than\\nmax_auto_margin_usdt\\n  Input should be greater than 0 [type=greater_than, input_value=Decimal('0.00'), input_type=Decimal]\\n    For further information visit https://errors.pydantic.dev/2.13/v/greater_than\\nmax_position_notional_usdt\\n  Input should be greater than 0 [type=greater_than, input_value=Decimal('0.00'), input_type=Decimal]\\n    For further information visit https://errors.pydantic.dev/2.13/v/greater_than", "to_state": "REJECTED", "from_state": "PREFLIGHT", "dispatch_id": "ff9a5f40-d9a3-40aa-984e-8628902c504b"}	1	2026-09-17 20:25:45.809258+00	2026-09-17 20:25:42.420253+00	\N	\N	\N	\N
2fe520c2-8a9f-4bc7-afa3-1a5d5813a306	analysis:7e4ee4f4-ff8b-4019-8e10-c43ea28d279b:42ebe02ecf5b6c2054e5f29cd11a364d0453bd32485b5747f460c8a59bd84392	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "Goodmorning $PUMP ❤️\\n\\nhttps://x.com/learnernoearner/status/2100518967836070266?s=46", "take_profits": [], "failure_class": "codex exited with status 1", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16150, "source_received_at": "2026-09-17T09:36:05+00:00"}	1	2026-09-17 09:36:48.962107+00	2026-09-17 09:36:39.480989+00	\N	\N	\N	\N
9619d8a1-e765-44dd-9f53-42f7bddad307	analysis:d84df5a3-6bab-4204-88a1-2ab185a14748:b3ec6b59939f6ff25ffaa5add5d3a371ecdc3e35e3c8d61467a4c1ba08ccd4a1	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "", "take_profits": [], "failure_class": "codex exited with status 1", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16149, "source_received_at": "2026-09-17T09:35:53+00:00"}	1	2026-09-17 09:36:49.287438+00	2026-09-17 09:36:39.480989+00	\N	\N	\N	\N
6219da66-cb25-40f4-9e17-33f16b79f702	analysis:7328ff35-0eb0-4cce-95bb-f2ee00aae80f:84f7c9437aaca5f320aa9c4f9ebd02870c82fdf3538d8572c0f0eb5e6b04634b	{"kind": "signal-analysis", "pair": "PENDLE", "entry": "2.32", "status": "FALLBACK_ACCEPTED", "direction": "LONG", "stop_loss": "2.2465", "dispatches": 1, "source_text": "#PENDLE $PENDLE LONG TRADE\\n\\nENTRY: 2.32\\n\\nTARGET: 3.22\\n\\nSTOPLOSS: 2.2465", "take_profits": ["3.22"], "failure_class": "codex exited with status 1", "canonical_signal": true, "management_action": null, "management_symbol": null, "source_message_id": 16152, "source_received_at": "2026-09-17T20:24:07+00:00"}	1	2026-09-17 20:25:14.873824+00	2026-09-17 20:25:04.654028+00	\N	\N	\N	\N
692a38e8-fec9-4044-af15-357815fa66c9	analysis:d10cddfa-4451-4112-a08d-a2834d83fd2e:d7e671ef38a9365f738551efb9f7b211c896e4906318369c754459e6af8725b9	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "$PENDLE TP1 here", "take_profits": [], "failure_class": "codex exited with status 1", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16154, "source_received_at": "2026-09-18T02:07:23+00:00"}	1	2026-09-18 02:08:09.984452+00	2026-09-18 02:08:01.512462+00	\N	\N	\N	\N
48fefe7c-8678-404a-9731-81e1955fd47a	dispatch-transition:259b562d-7834-44db-8c6f-9cd2605b8c15:QUEUED:PREFLIGHT	{"kind": "execution-event", "reason": "none", "to_state": "PREFLIGHT", "from_state": "QUEUED", "dispatch_id": "259b562d-7834-44db-8c6f-9cd2605b8c15"}	1	2026-09-18 02:24:17.818674+00	2026-09-18 02:24:13.407705+00	\N	\N	\N	\N
1695babd-9f31-4b2e-9d7e-5443a72d108e	analysis:be97cbb1-e7f3-484e-9aa6-aa1ae6e4d29e:79ae4ee93247de4aa00ad0def3bc4d4de5b38dac74d06f17a5bb03989214a0d8	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "$BTC short @ 80400\\n\\nSl above 81280", "take_profits": [], "failure_class": "codex exited with status 1", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16157, "source_received_at": "2026-09-18T13:53:43+00:00"}	1	\N	2026-09-18 13:54:40.602481+00	\N	\N	\N	2026-09-18 13:54:48.52989+00
a39d4646-9949-440e-b4ee-fb85ae82f3af	analysis:c30f710a-0835-47c2-8b25-1e493807a68b:30a8903f201a52d9cc4a02b1a2e194cca8c00120f393450002c9ceb943304532	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "Goodmorning❤️\\n\\n$BTC bounced at the midrange from our HTF update, as we mentioned that a long would be built there and now we are close to resistance again \\n\\nEverything is written in the update no need to explain again", "take_profits": [], "failure_class": "codex exited with status 1", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16159, "source_received_at": "2026-09-19T13:42:32+00:00"}	1	2026-09-19 13:43:44.250909+00	2026-09-19 13:43:33.807205+00	\N	\N	\N	\N
c270cde8-762e-47d6-8840-1f7c1ec13dff	dispatch-transition:cb58a3cb-5c50-47f5-8228-306ebca6e813:QUEUED:PREFLIGHT	{"kind": "execution-event", "reason": "none", "to_state": "PREFLIGHT", "from_state": "QUEUED", "dispatch_id": "cb58a3cb-5c50-47f5-8228-306ebca6e813"}	1	2026-09-19 18:36:53.018281+00	2026-09-19 18:36:50.233444+00	\N	\N	\N	\N
eaf20092-0244-46f6-a498-6a5c3c3b3628	dispatch-transition:cb58a3cb-5c50-47f5-8228-306ebca6e813:SUBMITTING:FILLED	{"kind": "execution-event", "reason": "fallback-protection-active", "to_state": "FILLED", "from_state": "SUBMITTING", "dispatch_id": "cb58a3cb-5c50-47f5-8228-306ebca6e813"}	1	2026-09-19 18:36:59.310679+00	2026-09-19 18:36:54.901293+00	\N	\N	\N	\N
e0eedbf7-2564-4ef5-b265-ce62e7c903f3	dispatch-transition:9c6d5119-4041-4951-af84-d9a2fceaeee6:PREFLIGHT:REJECTED	{"kind": "execution-event", "reason": "Bitget GET /api/v2/mix/account/account HTTP 400: 40034 Parameter BONKUSDT does not exist", "to_state": "REJECTED", "from_state": "PREFLIGHT", "dispatch_id": "9c6d5119-4041-4951-af84-d9a2fceaeee6"}	1	2026-09-20 17:19:47.628621+00	2026-09-20 17:19:46.632536+00	\N	\N	\N	\N
41d1ded6-8207-479b-b582-8615bd6ef258	bitget-dispatch:9c6d5119-4041-4951-af84-d9a2fceaeee6:Bitget GET /api/v2/mix/account/account HTTP 400: 40034 Parameter BONKUSDT does not exist	{"kind": "execution-alert", "reason": "Bitget GET /api/v2/mix/account/account HTTP 400: 40034 Parameter BONKUSDT does not exist", "dispatch_id": "9c6d5119-4041-4951-af84-d9a2fceaeee6"}	1	2026-09-20 17:19:47.9763+00	2026-09-20 17:19:46.660903+00	\N	\N	\N	\N
0eeaad8e-be6a-4092-ba21-a54a759d802f	dispatch-transition:23c8dc14-4553-454f-8eed-247da9e50b56:VALIDATED:SUBMITTING	{"kind": "execution-event", "reason": "none", "to_state": "SUBMITTING", "from_state": "VALIDATED", "dispatch_id": "23c8dc14-4553-454f-8eed-247da9e50b56"}	1	2026-09-12 11:42:06.106163+00	2026-09-12 11:42:00.484369+00	\N	\N	\N	\N
64e6c11d-a7f2-4c31-b2f9-58a7fe9688bb	dispatch-transition:23c8dc14-4553-454f-8eed-247da9e50b56:SUBMITTING:UNKNOWN	{"kind": "execution-event", "reason": "provider-unknown", "to_state": "UNKNOWN", "from_state": "SUBMITTING", "dispatch_id": "23c8dc14-4553-454f-8eed-247da9e50b56"}	1	2026-09-12 11:42:06.41715+00	2026-09-12 11:42:03.04649+00	\N	\N	\N	\N
b44fec8d-7fb7-4042-a0ca-3787c75e3d1b	analysis:214b7e8c-b624-4772-bcb7-9f704d46748a:9203f5d562b4a9fa53eb2880d7a554edab634c5c1743142bbc75caddea8543f5	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "book some profits on $EUL", "take_profits": [], "failure_class": "Only a request to book profits on EUL is present; no explicit side, entry, stop-loss, or take-profit price.", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16125, "source_received_at": "2026-09-12T18:10:54+00:00"}	1	2026-09-12 18:11:39.763994+00	2026-09-12 18:11:29.800547+00	\N	\N	\N	\N
14cade4c-643c-4f0c-b895-f02a45679e63	analysis:b0444f30-8d9d-4f80-b10b-61d1652c1bb6:71bbfc9a47f6ab2112771d5c16ebe498336838fd62e3537be57dbf0cd1530f6b	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "sl to entry $ETHFI", "take_profits": [], "failure_class": "Only an instruction to move stop loss to entry is present; no side, entry price, stop-loss price, or take-profit is explicitly provided.", "canonical_signal": false, "management_action": "SL_TO_ENTRY", "management_symbol": "SLUSDT", "source_message_id": 16123, "source_received_at": "2026-09-12T11:57:35+00:00"}	1	2026-09-12 11:58:36.60637+00	2026-09-12 11:58:23.929006+00	\N	\N	\N	\N
b93915c8-1427-4335-8519-ea8241f26c47	dispatch-transition:9ea05801-e066-409a-8817-83cac6dfc638:PREFLIGHT:SIZED	{"kind": "execution-event", "reason": "none", "to_state": "SIZED", "from_state": "PREFLIGHT", "dispatch_id": "9ea05801-e066-409a-8817-83cac6dfc638"}	1	2026-09-12 17:31:41.761749+00	2026-09-12 17:31:37.571449+00	\N	\N	\N	\N
5715c7f2-5c0b-4a23-a200-73ba1a07b156	analysis:ffafa787-e92a-47f7-adbb-6379dd0045aa:23c1c4c4a60a7ce4514b67b894d4c468aadc9718efe9466e0b2c980640731637	{"kind": "signal-analysis", "pair": "EUL", "entry": "1.268", "status": "CODEX_SUCCEEDED", "direction": "LONG", "stop_loss": "1.2365", "dispatches": 2, "source_text": "#EUL $EUL LONG TRADE\\n\\nENTRY: 1.268\\n\\nTARGET: 1.47\\n\\nSTOPLOSS: 1.2365", "take_profits": ["1.47"], "failure_class": "none", "canonical_signal": true, "management_action": null, "management_symbol": null, "source_message_id": 16124, "source_received_at": "2026-09-12T17:31:01+00:00"}	1	2026-09-12 17:31:26.133424+00	2026-09-12 17:31:16.618112+00	\N	\N	\N	\N
354c9ced-bc8a-419c-acce-1384ddc384b5	dispatch-transition:9ea05801-e066-409a-8817-83cac6dfc638:QUEUED:PREFLIGHT	{"kind": "execution-event", "reason": "none", "to_state": "PREFLIGHT", "from_state": "QUEUED", "dispatch_id": "9ea05801-e066-409a-8817-83cac6dfc638"}	1	2026-09-12 17:31:36.458068+00	2026-09-12 17:31:35.082227+00	\N	\N	\N	\N
f1412d0f-2e87-4158-b2b4-13461eb8faa5	dispatch-transition:9ea05801-e066-409a-8817-83cac6dfc638:SIZED:VALIDATED	{"kind": "execution-event", "reason": "none", "to_state": "VALIDATED", "from_state": "SIZED", "dispatch_id": "9ea05801-e066-409a-8817-83cac6dfc638"}	1	2026-09-12 17:31:42.050029+00	2026-09-12 17:31:37.590601+00	\N	\N	\N	\N
7b620c80-6309-4fa4-b3bd-693908b6d01a	dispatch-transition:9ea05801-e066-409a-8817-83cac6dfc638:VALIDATED:SUBMITTING	{"kind": "execution-event", "reason": "none", "to_state": "SUBMITTING", "from_state": "VALIDATED", "dispatch_id": "9ea05801-e066-409a-8817-83cac6dfc638"}	1	2026-09-12 17:31:42.339631+00	2026-09-12 17:31:37.626353+00	\N	\N	\N	\N
6e518cbe-7749-4fab-b7a9-f4d7c98dbf79	analysis:e7f89b7d-e8da-43c3-872a-d932564c0419:311a40ff51b8abb56a5cfd11ee6734814b5cb2b19ddc4b52322f8b2715dd2159	{"kind": "signal-analysis", "pair": "PONS", "entry": "0.584", "status": "CODEX_SUCCEEDED", "direction": "LONG", "stop_loss": "0.564", "dispatches": 2, "source_text": "#PONS $PONS LONG TRADE\\n\\nENTRY: 0.584\\n\\nTARGET: 0.747\\n\\nSTOPLOSS: 0.564", "take_profits": ["0.747"], "failure_class": "none", "canonical_signal": true, "management_action": null, "management_symbol": null, "source_message_id": 16126, "source_received_at": "2026-09-13T03:48:04+00:00"}	1	2026-09-13 03:48:19.337232+00	2026-09-13 03:48:08.35757+00	\N	\N	\N	\N
37e6ab57-f97e-4d6d-baf2-4e8f772e1476	dispatch-transition:9ea05801-e066-409a-8817-83cac6dfc638:SUBMITTING:UNKNOWN	{"kind": "execution-event", "reason": "provider-unknown", "to_state": "UNKNOWN", "from_state": "SUBMITTING", "dispatch_id": "9ea05801-e066-409a-8817-83cac6dfc638"}	1	2026-09-12 17:31:42.619187+00	2026-09-12 17:31:40.171946+00	\N	\N	\N	\N
7abbfd68-486c-470e-87e5-6591ced50f00	dispatch-transition:9e9516b4-c2de-4bf2-954c-73d5a41f3310:QUEUED:PREFLIGHT	{"kind": "execution-event", "reason": "none", "to_state": "PREFLIGHT", "from_state": "QUEUED", "dispatch_id": "9e9516b4-c2de-4bf2-954c-73d5a41f3310"}	1	2026-09-13 03:48:34.899539+00	2026-09-13 03:48:33.83587+00	\N	\N	\N	\N
4796570f-9528-4a19-b840-83dc65c2952b	dispatch-transition:9e9516b4-c2de-4bf2-954c-73d5a41f3310:SIZED:VALIDATED	{"kind": "execution-event", "reason": "none", "to_state": "VALIDATED", "from_state": "SIZED", "dispatch_id": "9e9516b4-c2de-4bf2-954c-73d5a41f3310"}	1	2026-09-13 03:48:40.492553+00	2026-09-13 03:48:36.209878+00	\N	\N	\N	\N
fc42898a-7483-4d36-bde8-591b4c38e8f7	dispatch-transition:9e9516b4-c2de-4bf2-954c-73d5a41f3310:PREFLIGHT:SIZED	{"kind": "execution-event", "reason": "none", "to_state": "SIZED", "from_state": "PREFLIGHT", "dispatch_id": "9e9516b4-c2de-4bf2-954c-73d5a41f3310"}	1	2026-09-13 03:48:40.190397+00	2026-09-13 03:48:36.188002+00	\N	\N	\N	\N
bed8f384-c28d-4f6b-b8bb-9cb39276e9e4	dispatch-transition:9e9516b4-c2de-4bf2-954c-73d5a41f3310:VALIDATED:REJECTED	{"kind": "execution-event", "reason": "canary-order-cap-reached", "to_state": "REJECTED", "from_state": "VALIDATED", "dispatch_id": "9e9516b4-c2de-4bf2-954c-73d5a41f3310"}	1	2026-09-13 03:48:40.806737+00	2026-09-13 03:48:36.248357+00	\N	\N	\N	\N
0fb32fb2-5bb6-45a6-8c4f-5617531392a4	analysis:c8249cd2-7024-4c3b-9246-ecaa9545be14:9a21f862e4569e051cef61a15cdf5ebc031b86ffe91e7e86a4f8e02c22d58ba8	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "CODEX_SUCCEEDED", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "$PONS did 3% then later dumped to sl", "take_profits": [], "failure_class": "none", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16127, "source_received_at": "2026-09-13T13:42:45+00:00"}	1	2026-09-13 13:43:02.139109+00	2026-09-13 13:42:47.354909+00	\N	\N	\N	\N
ca0fcd44-c174-40a6-9c70-9dfb47278f25	kill-switch:bitget:missing-stop-loss	{"kind": "kill-switch", "scope": "bitget", "reason": "missing-stop-loss"}	1	2026-09-13 14:28:55.542226+00	2026-09-13 14:28:52.866281+00	\N	\N	\N	\N
120368d6-3ddc-4d8d-bf27-1a444aa22ca6	bitget-dispatch:e92ed413-f6c0-4eb4-877a-d0e3840021be:missing-take-profits	{"kind": "execution-alert", "reason": "missing-take-profits", "dispatch_id": "e92ed413-f6c0-4eb4-877a-d0e3840021be"}	1	2026-09-14 22:39:05.564364+00	2026-09-14 22:39:02.031167+00	\N	\N	\N	\N
81b87ef5-b4c4-467c-bb33-9723ffed877f	dispatch-transition:e92ed413-f6c0-4eb4-877a-d0e3840021be:SIZED:VALIDATED	{"kind": "execution-event", "reason": "none", "to_state": "VALIDATED", "from_state": "SIZED", "dispatch_id": "e92ed413-f6c0-4eb4-877a-d0e3840021be"}	1	2026-09-14 22:42:32.463711+00	2026-09-14 22:42:29.893984+00	\N	\N	\N	\N
41fddcf5-f7b2-43fe-b0d5-0f1277e6aad7	analysis:ac5ebb01-8a3b-40af-bb84-c1145d204598:3886302802ed718b40cec19aa66a88b18552b7970089e48fdfa08275e0bd5f77	{"kind": "signal-analysis", "pair": "WLD", "entry": "0.396", "status": "CODEX_SUCCEEDED", "direction": "LONG", "stop_loss": "0.388", "dispatches": 1, "source_text": "#WLD $WLD LONG TRADE\\n\\nENTRY: 0.396\\n\\nTARGET: 0.427\\n\\nSTOPLOSS: 0.388", "take_profits": ["0.427"], "failure_class": "none", "canonical_signal": true, "management_action": null, "management_symbol": null, "source_message_id": 16128, "source_received_at": "2026-09-13T14:27:48+00:00"}	1	2026-09-13 14:28:22.565442+00	2026-09-13 14:28:14.235515+00	\N	\N	\N	\N
19ab79b1-91f2-43a4-afb1-fbdd54147ab0	dispatch-transition:cb58a3cb-5c50-47f5-8228-306ebca6e813:PREFLIGHT:SIZED	{"kind": "execution-event", "reason": "none", "to_state": "SIZED", "from_state": "PREFLIGHT", "dispatch_id": "cb58a3cb-5c50-47f5-8228-306ebca6e813"}	1	2026-09-19 18:36:53.35776+00	2026-09-19 18:36:52.121867+00	\N	\N	\N	\N
1d794340-ef55-4a8d-a925-747a61e98eeb	dispatch-transition:8d61b0c7-1cfe-4d01-8503-8a5394f9dc71:QUEUED:PREFLIGHT	{"kind": "execution-event", "reason": "none", "to_state": "PREFLIGHT", "from_state": "QUEUED", "dispatch_id": "8d61b0c7-1cfe-4d01-8503-8a5394f9dc71"}	1	2026-09-13 14:28:32.948857+00	2026-09-13 14:28:30.495216+00	\N	\N	\N	\N
88798bc5-be19-4f5f-ad32-1b7f27dcd1e1	dispatch-transition:9ea05801-e066-409a-8817-83cac6dfc638:UNKNOWN:RECONCILED	{"kind": "execution-event", "reason": "approved-provider-flat-readback-no-order-or-intent", "to_state": "RECONCILED", "from_state": "UNKNOWN", "dispatch_id": "9ea05801-e066-409a-8817-83cac6dfc638"}	1	2026-09-14 06:39:26.019376+00	2026-09-14 06:39:20.64466+00	\N	\N	\N	\N
c5f13015-bf7c-435c-bd3d-73696354c0ee	dispatch-transition:8d61b0c7-1cfe-4d01-8503-8a5394f9dc71:PREFLIGHT:SIZED	{"kind": "execution-event", "reason": "none", "to_state": "SIZED", "from_state": "PREFLIGHT", "dispatch_id": "8d61b0c7-1cfe-4d01-8503-8a5394f9dc71"}	1	2026-09-13 14:28:33.252385+00	2026-09-13 14:28:32.493068+00	\N	\N	\N	\N
eeeb2b29-b123-4379-8810-c12f3823d774	analysis:5a854c73-284b-47f9-85a2-a09d45f26ce6:73a2cb918d067c4f7d41253e6a4d0329f99735980457dea72825747bfbbe9c8f	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "CODEX_SUCCEEDED", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "Goodmorning❤️\\n\\nhttps://x.com/learnernoearner/status/2099159961204838555?s=46", "take_profits": [], "failure_class": "none", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16129, "source_received_at": "2026-09-13T15:35:25+00:00"}	1	2026-09-13 15:35:58.189217+00	2026-09-13 15:35:50.899125+00	\N	\N	\N	\N
d73d7bcf-3da9-418d-85c6-3080245b0acf	dispatch-transition:8d61b0c7-1cfe-4d01-8503-8a5394f9dc71:SIZED:VALIDATED	{"kind": "execution-event", "reason": "none", "to_state": "VALIDATED", "from_state": "SIZED", "dispatch_id": "8d61b0c7-1cfe-4d01-8503-8a5394f9dc71"}	1	2026-09-13 14:28:33.539965+00	2026-09-13 14:28:32.509082+00	\N	\N	\N	\N
423c68ed-cbb7-4eab-8f24-e190b6f5a323	dispatch-transition:8d61b0c7-1cfe-4d01-8503-8a5394f9dc71:VALIDATED:SUBMITTING	{"kind": "execution-event", "reason": "none", "to_state": "SUBMITTING", "from_state": "VALIDATED", "dispatch_id": "8d61b0c7-1cfe-4d01-8503-8a5394f9dc71"}	1	2026-09-13 14:28:34.876175+00	2026-09-13 14:28:32.544742+00	\N	\N	\N	\N
046e4f5c-8cdd-4ca9-a727-1adc677bed39	dispatch-transition:8d61b0c7-1cfe-4d01-8503-8a5394f9dc71:SUBMITTING:UNKNOWN	{"kind": "execution-event", "reason": "provider-unknown", "to_state": "UNKNOWN", "from_state": "SUBMITTING", "dispatch_id": "8d61b0c7-1cfe-4d01-8503-8a5394f9dc71"}	1	2026-09-13 14:28:40.198034+00	2026-09-13 14:28:35.309731+00	\N	\N	\N	\N
224b85ad-9a69-4730-8425-5cf5db00d600	dispatch-transition:8d61b0c7-1cfe-4d01-8503-8a5394f9dc71:UNKNOWN:FILLED	{"kind": "execution-event", "reason": "entry-filled-fallback-protection-active", "to_state": "FILLED", "from_state": "UNKNOWN", "dispatch_id": "8d61b0c7-1cfe-4d01-8503-8a5394f9dc71"}	1	2026-09-13 14:45:31.577474+00	2026-09-13 14:45:30.609448+00	\N	\N	\N	\N
843e08dc-d21d-4b51-8b85-6b5ce8af5ee9	dispatch-transition:c0000000-0000-0000-0000-000000000001:UNKNOWN:RECONCILED	{"kind": "execution-event", "reason": "approved-provider-flat-readback-no-order-or-intent", "to_state": "RECONCILED", "from_state": "UNKNOWN", "dispatch_id": "c0000000-0000-0000-0000-000000000001"}	1	2026-09-14 06:39:25.207184+00	2026-09-14 06:39:20.588724+00	\N	\N	\N	\N
434cdaa3-6b0d-4ff4-91a8-e36981bf6f55	kill-switch-release:bitget:hernanda-approved-live-reopen-20260914	{"kind": "kill-switch-release", "scope": "bitget", "approval_reference": "hernanda-approved-live-reopen-20260914"}	1	2026-09-14 06:39:26.359074+00	2026-09-14 06:39:20.66446+00	\N	\N	\N	\N
6f74126a-06ba-416c-833e-9b8bbfb6b59a	dispatch-transition:02de5cb4-8e11-4916-929b-6d95c1b51b1f:UNKNOWN:RECONCILED	{"kind": "execution-event", "reason": "approved-provider-flat-readback-no-order-or-intent", "to_state": "RECONCILED", "from_state": "UNKNOWN", "dispatch_id": "02de5cb4-8e11-4916-929b-6d95c1b51b1f"}	1	2026-09-14 06:39:25.635721+00	2026-09-14 06:39:20.618925+00	\N	\N	\N	\N
83fa54fb-91a3-4aa2-843a-e08e1a74e8ef	analysis:8d6e5815-2c41-48b4-aab3-ee74b32a9747:10bb3ce408ffe9b85ec092eff19b3cf08e031c60ec5033ea94935519f310b908	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "Goodmorning❤️\\n\\nNow that the weekend is over lets get back to work, will start with doing $BTC first then we trade\\n\\nMissed sharing $CL trade yesterday night but i feel as sleep😭\\n\\nhttps://x.com/learnernoearner/status/2099483352935936211?s=46", "take_profits": [], "failure_class": "codex exited with status 1", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16131, "source_received_at": "2026-09-14T13:00:21+00:00"}	1	2026-09-14 13:00:38.616358+00	2026-09-14 13:00:32.62544+00	\N	\N	\N	\N
544540ed-973f-4ed2-a713-6b3c47d149a8	analysis:db3099a9-cc77-4bc7-8b56-01618057f247:28c2c1a24d6ace5b6a9d7bcbcdc574c4bb080c9cab2087838fef663f7964380d	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "$WLD sl hit", "take_profits": [], "failure_class": "codex exited with status 1", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16130, "source_received_at": "2026-09-14T12:57:23+00:00"}	1	2026-09-14 12:57:47.435211+00	2026-09-14 12:57:36.162967+00	\N	\N	\N	\N
cdfdf8d1-1407-45c7-9a2c-afb20455df93	analysis:e1ccfe43-e8f8-40f6-9bbf-f13ccbfac1f7:687baa6a1d43a0a1e12dabbaad220d6b6dec7e0dc160cbaa0cccf46d48c0da3d	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "$BTC todays move\\n\\nhttps://x.com/learnernoearner/status/2099497373311369385?s=46", "take_profits": [], "failure_class": "codex exited with status 1", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16132, "source_received_at": "2026-09-14T13:56:04+00:00"}	1	2026-09-14 13:56:09.97295+00	2026-09-14 13:56:06.264138+00	\N	\N	\N	\N
39ccc64b-b276-4aee-9d37-f8ed163366d1	analysis:f2c137d9-871a-426b-92c1-9094c46d6dbb:1475a9a58b03a4864980b730227894efff93e6ba2d29a3a95129b0fde6bf79c6	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "Shorting $WLD here around 0.385\\n\\nStoploss: 0.3938", "take_profits": [], "failure_class": "codex exited with status 1", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16133, "source_received_at": "2026-09-14T14:22:28+00:00"}	1	2026-09-14 14:22:43.330752+00	2026-09-14 14:22:36.733622+00	\N	\N	\N	\N
9e584b26-562a-44df-913b-89f1aabd6037	dispatch-transition:e92ed413-f6c0-4eb4-877a-d0e3840021be:QUEUED:REJECTED	{"kind": "execution-event", "reason": "missing-take-profits", "to_state": "REJECTED", "from_state": "QUEUED", "dispatch_id": "e92ed413-f6c0-4eb4-877a-d0e3840021be"}	1	2026-09-14 22:39:05.234433+00	2026-09-14 22:39:01.924439+00	\N	\N	\N	\N
58e4564a-5df1-4603-94f0-1269f92eb409	dispatch-transition:ff9a5f40-d9a3-40aa-984e-8628902c504b:QUEUED:PREFLIGHT	{"kind": "execution-event", "reason": "none", "to_state": "PREFLIGHT", "from_state": "QUEUED", "dispatch_id": "ff9a5f40-d9a3-40aa-984e-8628902c504b"}	1	2026-09-17 20:25:40.300992+00	2026-09-17 20:25:39.752514+00	\N	\N	\N	\N
a1f82f7f-74aa-4caf-b743-fd394a627028	dispatch-transition:e92ed413-f6c0-4eb4-877a-d0e3840021be:QUEUED:PREFLIGHT	{"kind": "execution-event", "reason": "none", "to_state": "PREFLIGHT", "from_state": "QUEUED", "dispatch_id": "e92ed413-f6c0-4eb4-877a-d0e3840021be"}	1	2026-09-14 22:42:31.812604+00	2026-09-14 22:42:28.488447+00	\N	\N	\N	\N
09257000-1a3d-4f4c-94af-8e404a7a78c3	dispatch-transition:e92ed413-f6c0-4eb4-877a-d0e3840021be:PREFLIGHT:SIZED	{"kind": "execution-event", "reason": "none", "to_state": "SIZED", "from_state": "PREFLIGHT", "dispatch_id": "e92ed413-f6c0-4eb4-877a-d0e3840021be"}	1	2026-09-14 22:42:32.122878+00	2026-09-14 22:42:29.878031+00	\N	\N	\N	\N
60035081-702c-4ed9-9405-7395f71b41db	dispatch-transition:259b562d-7834-44db-8c6f-9cd2605b8c15:PREFLIGHT:REJECTED	{"kind": "execution-event", "reason": "Bitget GET /api/v2/mix/account/account HTTP 400: 40309 The symbol has been removed", "to_state": "REJECTED", "from_state": "PREFLIGHT", "dispatch_id": "259b562d-7834-44db-8c6f-9cd2605b8c15"}	1	2026-09-18 02:24:23.75403+00	2026-09-18 02:24:14.329792+00	\N	\N	\N	\N
3994c250-2bfd-4952-9b9f-0d9431899499	dispatch-transition:e92ed413-f6c0-4eb4-877a-d0e3840021be:VALIDATED:SUBMITTING	{"kind": "execution-event", "reason": "none", "to_state": "SUBMITTING", "from_state": "VALIDATED", "dispatch_id": "e92ed413-f6c0-4eb4-877a-d0e3840021be"}	1	2026-09-14 22:42:32.736698+00	2026-09-14 22:42:29.927554+00	\N	\N	\N	\N
806784c9-5247-4842-bf08-1afa930f35a3	bitget-dispatch:a12e4cfe-9e88-4406-8e18-6cce9549e1b7:kill-switch-latched	{"kind": "execution-alert", "reason": "kill-switch-latched", "dispatch_id": "a12e4cfe-9e88-4406-8e18-6cce9549e1b7"}	1	2026-09-15 14:09:57.78194+00	2026-09-15 14:09:56.323983+00	\N	\N	\N	\N
779613e1-8a0d-4c39-b000-06dde5835099	dispatch-transition:e92ed413-f6c0-4eb4-877a-d0e3840021be:SUBMITTING:FILLED	{"kind": "execution-event", "reason": "fallback-protection-active", "to_state": "FILLED", "from_state": "SUBMITTING", "dispatch_id": "e92ed413-f6c0-4eb4-877a-d0e3840021be"}	1	2026-09-14 22:42:32.997698+00	2026-09-14 22:42:32.344604+00	\N	\N	\N	\N
9046684c-14f7-4dd4-bc66-1f0b3223fee7	dispatch-transition:9c6d5119-4041-4951-af84-d9a2fceaeee6:QUEUED:PREFLIGHT	{"kind": "execution-event", "reason": "none", "to_state": "PREFLIGHT", "from_state": "QUEUED", "dispatch_id": "9c6d5119-4041-4951-af84-d9a2fceaeee6"}	1	2026-09-20 17:19:47.288657+00	2026-09-20 17:19:46.015302+00	\N	\N	\N	\N
5bef3a5d-5b76-44e8-8d25-67e44807c598	analysis:9ac2cf12-42f4-4941-9600-b348488a8c8f:ffee46a5047c85dd10c00a9d6756a23e05a0dbba39266c9376b73751a866e6e0	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "$BTC dropped 3%+ from our level and its almost back to the 76.3k support if we dont hold this level then 75k will be the key level between us and the goblin town\\n\\nAnyways will let this setup play out and analyse $ETH today\\n\\nhttps://x.com/learnernoearner/status/2099835727265542447?s=46", "take_profits": [], "failure_class": "codex exited with status 1", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16136, "source_received_at": "2026-09-15T12:20:37+00:00"}	1	2026-09-15 12:21:35.741532+00	2026-09-15 12:21:25.582539+00	\N	\N	\N	\N
e1a58a73-0c1a-493d-b23b-1f5dcac1c862	analysis:d796ea2d-fa83-42dc-b607-9f6b5c276ede:20f8143977a56b0e052774cc12d4bb8509a313937c705817d78839126f2223fa	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "$WLD invalid", "take_profits": [], "failure_class": "codex exited with status 1", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16134, "source_received_at": "2026-09-14T23:12:14+00:00"}	1	2026-09-14 23:13:07.113184+00	2026-09-14 23:12:58.996929+00	\N	\N	\N	\N
5d1bc57b-db0d-449b-bbe2-cf706f9857e5	analysis:fe50b07e-1777-4ef4-a695-de6912c898c4:6f0cec363ea77c768d30e80d131a1e027c7e40e01a057cafd24d4dcf74d9233a	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "$BTC todays move played out as expected\\n\\nhttps://x.com/learnernoearner/status/2099671761348448468?s=46", "take_profits": [], "failure_class": "codex exited with status 1", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16135, "source_received_at": "2026-09-15T01:29:10+00:00"}	1	2026-09-15 01:30:09.548168+00	2026-09-15 01:30:01.761583+00	\N	\N	\N	\N
a757c46a-1317-4e27-a8a0-4d3f44372522	analysis:d5fab280-0de5-4050-a55d-c36ded76cd58:959de2ad5273b8f419be00253738cdcdf16498efb34cd8bbdd516a8f23209770	{"kind": "signal-analysis", "pair": "SNDK", "entry": "1560", "status": "FALLBACK_ACCEPTED", "direction": "LONG", "stop_loss": "1535", "dispatches": 1, "source_text": "$SNDK LONG TRADE\\n\\nENTRY: 1560\\n\\nTARGET: 1795\\n\\nSTOPLOSS: 1535", "take_profits": ["1795"], "failure_class": "codex exited with status 1", "canonical_signal": true, "management_action": null, "management_symbol": null, "source_message_id": 16138, "source_received_at": "2026-09-15T14:09:19+00:00"}	1	2026-09-15 14:09:36.93041+00	2026-09-15 14:09:27.432029+00	\N	\N	\N	\N
90252343-d93f-4fac-a082-3a3de2bae6b5	analysis:1300390c-5300-49ca-9ce4-ade0dcf576e4:8c08b2e093ab9511e79dedc1ba02b0b1a479ec24ac3e94996c5c8da59e1b6436	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "$ETH todays move", "take_profits": [], "failure_class": "codex exited with status 1", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16137, "source_received_at": "2026-09-15T12:58:42+00:00"}	1	2026-09-15 12:59:32.31232+00	2026-09-15 12:59:26.228542+00	\N	\N	\N	\N
4331b425-d66e-477e-bfea-cde27ba74107	dispatch-transition:a12e4cfe-9e88-4406-8e18-6cce9549e1b7:QUEUED:REJECTED	{"kind": "execution-event", "reason": "kill-switch-latched", "to_state": "REJECTED", "from_state": "QUEUED", "dispatch_id": "a12e4cfe-9e88-4406-8e18-6cce9549e1b7"}	1	2026-09-15 14:09:57.285552+00	2026-09-15 14:09:56.298595+00	\N	\N	\N	\N
3846d972-720a-4ca2-b603-5e789e95c1e0	dispatch-transition:a12e4cfe-9e88-4406-8e18-6cce9549e1b7:QUEUED:PREFLIGHT	{"kind": "execution-event", "reason": "none", "to_state": "PREFLIGHT", "from_state": "QUEUED", "dispatch_id": "a12e4cfe-9e88-4406-8e18-6cce9549e1b7"}	1	2026-09-15 14:27:28.427752+00	2026-09-15 14:27:26.837154+00	\N	\N	\N	\N
96feb37f-0851-41c4-ad95-86978cbe2ab1	dispatch-transition:a12e4cfe-9e88-4406-8e18-6cce9549e1b7:PREFLIGHT:SIZED	{"kind": "execution-event", "reason": "none", "to_state": "SIZED", "from_state": "PREFLIGHT", "dispatch_id": "a12e4cfe-9e88-4406-8e18-6cce9549e1b7"}	1	2026-09-15 14:27:33.739219+00	2026-09-15 14:27:28.916585+00	\N	\N	\N	\N
fe01123e-8e04-4abb-a00d-f1e828b2a9fe	dispatch-transition:a12e4cfe-9e88-4406-8e18-6cce9549e1b7:SIZED:VALIDATED	{"kind": "execution-event", "reason": "none", "to_state": "VALIDATED", "from_state": "SIZED", "dispatch_id": "a12e4cfe-9e88-4406-8e18-6cce9549e1b7"}	1	2026-09-15 14:27:34.024493+00	2026-09-15 14:27:28.929845+00	\N	\N	\N	\N
16e858c4-7ce6-437f-9535-cff5ec9f18d1	dispatch-transition:a12e4cfe-9e88-4406-8e18-6cce9549e1b7:VALIDATED:SUBMITTING	{"kind": "execution-event", "reason": "none", "to_state": "SUBMITTING", "from_state": "VALIDATED", "dispatch_id": "a12e4cfe-9e88-4406-8e18-6cce9549e1b7"}	1	2026-09-15 14:27:34.305039+00	2026-09-15 14:27:28.960561+00	\N	\N	\N	\N
b6720ebb-adb0-4971-9922-31c2b942cdcd	dispatch-transition:a12e4cfe-9e88-4406-8e18-6cce9549e1b7:SUBMITTING:FILLED	{"kind": "execution-event", "reason": "fallback-protection-active", "to_state": "FILLED", "from_state": "SUBMITTING", "dispatch_id": "a12e4cfe-9e88-4406-8e18-6cce9549e1b7"}	1	2026-09-15 14:27:34.62416+00	2026-09-15 14:27:31.508046+00	\N	\N	\N	\N
2ee53d2d-53e4-41da-9969-6119583ba25c	analysis:83a33e8c-82fc-4fc1-91d9-c76d6dc16c2e:3625aecd3b3702920feb61ac13af03dd39802397132275dc2221f1f57b3cbd43	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "$BTC done ✅ \\n\\nSee yall tomorrow with a new update", "take_profits": [], "failure_class": "codex exited with status 1", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16139, "source_received_at": "2026-09-15T14:54:30+00:00"}	1	2026-09-15 14:54:53.053074+00	2026-09-15 14:54:46.313466+00	\N	\N	\N	\N
39d44603-910b-4c50-a2c9-c24643693b3f	analysis:325c2c05-2255-4a6a-8e46-791a109cfaba:62ddabd89408a1de6cf7a290062afbffd3cc14ddc68095fd243858c7d4f877d1	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "$ETH done too \\n\\nCalled both dump on $ETH & $BTC", "take_profits": [], "failure_class": "codex exited with status 1", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16140, "source_received_at": "2026-09-15T14:55:03+00:00"}	1	2026-09-15 14:55:33.562539+00	2026-09-15 14:55:30.199754+00	\N	\N	\N	\N
c5f0b41f-f66f-40dc-92eb-8c833752e490	analysis:6256a540-bf35-4684-ada7-de973197a068:6c1dd959c6201637b0c95ffa36c3324fb714a169277ef24bdd78e14451826a54	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "SNDK hit sl", "take_profits": [], "failure_class": "codex exited with status 1", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16141, "source_received_at": "2026-09-15T16:03:14+00:00"}	1	2026-09-15 16:03:41.032061+00	2026-09-15 16:03:33.687581+00	\N	\N	\N	\N
4f14224c-e988-4e84-94ae-55afb6e7df5e	bitget-dispatch:259b562d-7834-44db-8c6f-9cd2605b8c15:Bitget GET /api/v2/mix/account/account HTTP 400: 40309 The symbol has been removed	{"kind": "execution-alert", "reason": "Bitget GET /api/v2/mix/account/account HTTP 400: 40309 The symbol has been removed", "dispatch_id": "259b562d-7834-44db-8c6f-9cd2605b8c15"}	1	2026-09-18 02:24:28.786412+00	2026-09-18 02:24:16.263701+00	\N	\N	\N	\N
90ee35f9-d6cb-43eb-96ab-9a37263d32cc	dispatch-transition:cb58a3cb-5c50-47f5-8228-306ebca6e813:SIZED:VALIDATED	{"kind": "execution-event", "reason": "none", "to_state": "VALIDATED", "from_state": "SIZED", "dispatch_id": "cb58a3cb-5c50-47f5-8228-306ebca6e813"}	1	2026-09-19 18:36:53.679149+00	2026-09-19 18:36:52.151781+00	\N	\N	\N	\N
4670c6bc-b3c0-49c6-9aed-b72a6e0b021f	analysis:c9cfc94a-d500-42c8-b12f-16bc9172c129:9e3b29dc3e0bcef60779c4152cd313676c69c065307fb474a9d9974a851bcea9	{"kind": "signal-analysis", "pair": "TAO", "entry": "224.5", "status": "FALLBACK_ACCEPTED", "direction": "LONG", "stop_loss": "218.55", "dispatches": 1, "source_text": "#TAO $TAO LONG TRADE\\n\\nENTRY: 224.5\\n\\nTARGET: 236.9\\n\\nSTOPLOSS: 218.55", "take_profits": ["236.9"], "failure_class": "codex exited with status 1", "canonical_signal": true, "management_action": null, "management_symbol": null, "source_message_id": 16142, "source_received_at": "2026-09-15T16:17:23+00:00"}	1	2026-09-15 16:17:35.295456+00	2026-09-15 16:17:30.623636+00	\N	\N	\N	\N
73ecfd53-5e38-40d4-86fd-ae0488d05380	dispatch-transition:7ecac60d-696d-4dcb-8fe3-34f3307ad596:SUBMITTING:UNKNOWN	{"kind": "execution-event", "reason": "provider-readback-error:RuntimeError", "to_state": "UNKNOWN", "from_state": "SUBMITTING", "dispatch_id": "7ecac60d-696d-4dcb-8fe3-34f3307ad596"}	1	2026-09-15 16:17:47.445856+00	2026-09-15 16:17:44.407651+00	\N	\N	\N	\N
19cadba9-868f-4ee0-b3c5-98cf9dbff8c6	dispatch-transition:7ecac60d-696d-4dcb-8fe3-34f3307ad596:QUEUED:PREFLIGHT	{"kind": "execution-event", "reason": "none", "to_state": "PREFLIGHT", "from_state": "QUEUED", "dispatch_id": "7ecac60d-696d-4dcb-8fe3-34f3307ad596"}	1	2026-09-15 16:17:45.63404+00	2026-09-15 16:17:42.37052+00	\N	\N	\N	\N
6e15a746-af13-40ef-9af0-5f44a163eb7f	dispatch-transition:7ecac60d-696d-4dcb-8fe3-34f3307ad596:PREFLIGHT:SIZED	{"kind": "execution-event", "reason": "none", "to_state": "SIZED", "from_state": "PREFLIGHT", "dispatch_id": "7ecac60d-696d-4dcb-8fe3-34f3307ad596"}	1	2026-09-15 16:17:45.955708+00	2026-09-15 16:17:44.307913+00	\N	\N	\N	\N
57378e8a-ad97-4c8d-8662-912b039d42c3	bitget-dispatch:7ecac60d-696d-4dcb-8fe3-34f3307ad596:provider-readback-error:RuntimeError	{"kind": "execution-alert", "reason": "provider-readback-error:RuntimeError", "dispatch_id": "7ecac60d-696d-4dcb-8fe3-34f3307ad596"}	1	2026-09-15 16:17:47.71867+00	2026-09-15 16:17:44.422722+00	\N	\N	\N	\N
839d1ebe-965b-48a0-9f96-8feaf2e1f068	dispatch-transition:7ecac60d-696d-4dcb-8fe3-34f3307ad596:SIZED:VALIDATED	{"kind": "execution-event", "reason": "none", "to_state": "VALIDATED", "from_state": "SIZED", "dispatch_id": "7ecac60d-696d-4dcb-8fe3-34f3307ad596"}	1	2026-09-15 16:17:46.883277+00	2026-09-15 16:17:44.331595+00	\N	\N	\N	\N
0d22a978-fcea-4d1b-9b05-aec371235d29	dispatch-transition:7ecac60d-696d-4dcb-8fe3-34f3307ad596:VALIDATED:SUBMITTING	{"kind": "execution-event", "reason": "none", "to_state": "SUBMITTING", "from_state": "VALIDATED", "dispatch_id": "7ecac60d-696d-4dcb-8fe3-34f3307ad596"}	1	2026-09-15 16:17:47.156405+00	2026-09-15 16:17:44.375735+00	\N	\N	\N	\N
d5b84136-998e-411e-9c55-fc6d96489e94	analysis:28d1be3a-92a6-4144-a157-899d943b7cf3:1fac41fab67d6f990f690e315fd64057c5c5a3f822c4228164fc6c3708b75412	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "$BTC todays move\\n\\nBids placed around 74k area", "take_profits": [], "failure_class": "codex exited with status 1", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16144, "source_received_at": "2026-09-16T14:13:37+00:00"}	1	2026-09-16 14:14:21.602264+00	2026-09-16 14:14:13.824696+00	\N	\N	\N	\N
3d3d8b96-6e04-4c61-8a7a-dd4d2f64135b	analysis:7ecb6599-d9de-4afb-9649-228d2572c53f:5caf848809a02cb3c07843aded35d6bd88e6a28ebc575257b935bb8697b70002	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "$TAO sl hit", "take_profits": [], "failure_class": "codex exited with status 1", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16143, "source_received_at": "2026-09-16T12:51:08+00:00"}	1	2026-09-16 12:51:26.040753+00	2026-09-16 12:51:15.064044+00	\N	\N	\N	\N
0a611183-290c-4cbb-8106-755fe0fb57e7	analysis:43de9ca9-8a25-4049-8521-8952f18d5dcf:f07f8884064ea8f4aa06300d27487fe87111357f4522c24cb55354fdc69fd17b	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "$PUMP hold above the box and we will see 0.004+ quickly", "take_profits": [], "failure_class": "codex exited with status 1", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16145, "source_received_at": "2026-09-16T16:50:35+00:00"}	1	2026-09-16 16:50:48.678965+00	2026-09-16 16:50:39.654698+00	\N	\N	\N	\N
94449b8b-caa9-4233-89c9-e8f4388e8c04	analysis:0ccd450e-a631-414c-b2fc-97b463788489:45c6353b18d7591d78d30f690730dc25544655daf4ebb2d38079ab72fffe7dba	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "$BTC shorted based on our plan\\n\\nhttps://x.com/learnernoearner/status/2100298326767817025?s=46", "take_profits": [], "failure_class": "codex exited with status 1", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16146, "source_received_at": "2026-09-16T18:59:10+00:00"}	1	2026-09-16 18:59:33.750979+00	2026-09-16 18:59:24.916612+00	\N	\N	\N	\N
3c0908ee-44b1-4754-ba4b-06f17870df23	analysis:663fc8e1-216d-4852-8968-06419dc0bfab:d185b60ab8bad5937c2924f9f1c168b948cf07a7c123b2a959f9595d45e5c4e4	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "Main plan was to long lower but did a small short scalp", "take_profits": [], "failure_class": "codex exited with status 1", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16147, "source_received_at": "2026-09-16T19:04:37+00:00"}	1	2026-09-16 19:04:50.662682+00	2026-09-16 19:04:45.84392+00	\N	\N	\N	\N
ce6c990d-4bfc-4ec9-86f0-dba1601e093e	analysis:c3ef821d-fadb-4f27-83d1-fa94f547e6eb:ca992d86cec8eac7b3500f8958087563f02d222e57a52bd821dd0309eb521ef8	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "$PUMP 15% in a day, full tp done", "take_profits": [], "failure_class": "codex exited with status 1", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16151, "source_received_at": "2026-09-17T15:51:39+00:00"}	1	2026-09-17 15:52:04.776976+00	2026-09-17 15:51:54.519694+00	\N	\N	\N	\N
80a29a10-9de2-4b4d-ad60-b0e14cb339c3	analysis:0656c4cf-fa1c-4510-862e-a41c636adfa8:400abe1e9fe01369e67605d301b205d09e61ded77ce08105b52ac3f5e2c6e508	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "$PENDLE sl to entry", "take_profits": [], "failure_class": "codex exited with status 1", "canonical_signal": false, "management_action": "SL_TO_ENTRY", "management_symbol": "PENDLEUSDT", "source_message_id": 16153, "source_received_at": "2026-09-18T01:52:30+00:00"}	1	2026-09-18 01:52:39.716506+00	2026-09-18 01:52:31.740788+00	\N	\N	\N	\N
e7d91266-d8f5-4e16-970f-4c093635b11f	bitget-dispatch:ff9a5f40-d9a3-40aa-984e-8628902c504b:3 validation errors for VenueRiskConfig\nbase_margin_usdt\n  Input should be greater than 0 [type=greater_than, input_value=Decimal('0.00'), input_type=Decimal]\n    For further information visit https://errors.pydantic.dev/2.13/v/greater_than\nmax_auto_margin_usdt\n  Input should be greater than 0 [type=greater_than, input_value=Decimal('0.00'), input_type=Decimal]\n    For further information visit https://errors.pydantic.dev/2.13/v/greater_than\nmax_position_notional_usdt\n  Input should be greater than 0 [type=greater_than, input_value=Decimal('0.00'), input_type=Decimal]\n    For further information visit https://errors.pydantic.dev/2.13/v/greater_than	{"kind": "execution-alert", "reason": "3 validation errors for VenueRiskConfig\\nbase_margin_usdt\\n  Input should be greater than 0 [type=greater_than, input_value=Decimal('0.00'), input_type=Decimal]\\n    For further information visit https://errors.pydantic.dev/2.13/v/greater_than\\nmax_auto_margin_usdt\\n  Input should be greater than 0 [type=greater_than, input_value=Decimal('0.00'), input_type=Decimal]\\n    For further information visit https://errors.pydantic.dev/2.13/v/greater_than\\nmax_position_notional_usdt\\n  Input should be greater than 0 [type=greater_than, input_value=Decimal('0.00'), input_type=Decimal]\\n    For further information visit https://errors.pydantic.dev/2.13/v/greater_than", "dispatch_id": "ff9a5f40-d9a3-40aa-984e-8628902c504b"}	1	2026-09-17 20:25:46.117827+00	2026-09-17 20:25:42.437871+00	\N	\N	\N	\N
dfe50b50-2541-438c-a55b-2ed31a3a6007	analysis:d17659ad-c1ee-4c2d-b49f-aa894246ddf3:a888af4db7c17b1ab1776498824f297f1c2fe03c5dced816436dc4df23710259	{"kind": "signal-analysis", "pair": "SAGA", "entry": "0.0221", "status": "FALLBACK_ACCEPTED", "direction": "LONG", "stop_loss": "0.02112", "dispatches": 1, "source_text": "#SAGA $SAGA LONG TRADE\\n\\nENTRY: 0.0221 - 0.02155\\n\\nTARGET: 0.036\\n\\nSTOPLOSS: 0.02112", "take_profits": ["0.036"], "failure_class": "codex exited with status 1", "canonical_signal": true, "management_action": null, "management_symbol": null, "source_message_id": 16155, "source_received_at": "2026-09-18T02:23:12+00:00"}	1	2026-09-18 02:23:48.043223+00	2026-09-18 02:23:19.787173+00	\N	\N	\N	\N
291aa712-c4da-42ea-a5f9-15c6ff674378	analysis:415aeaa7-7f63-4786-b610-a7b19b0e8552:9b652e19f0ad17b600a27b002e2bd6f960d691bf529dfc9d38a4ae45508e6423	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "$PENDLE 16% fully closed here\\n\\n$SAGA pumped 1R after filling both entries then dumped", "take_profits": [], "failure_class": "codex exited with status 1", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16156, "source_received_at": "2026-09-18T12:54:59+00:00"}	1	\N	2026-09-18 12:55:37.806609+00	\N	\N	\N	2026-09-18 12:55:49.207338+00
6f8aac61-2b9e-42ed-a239-6372627f6267	analysis:5766463d-3931-4a12-8c60-852c775a32cf:8033fabe20522b65701c43a408e2cfab84b7dfb91186f3ad104ea28feb863520	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "closing my short here at 81k\\n\\nWas wrong on this one", "take_profits": [], "failure_class": "codex exited with status 1", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16158, "source_received_at": "2026-09-18T16:34:43+00:00"}	1	2026-09-18 16:35:55.146724+00	2026-09-18 16:35:46.378543+00	\N	\N	\N	\N
5c3e973e-2a17-4563-99eb-5637bc2e2ecb	analysis:f0e2b989-749b-4fc0-a0ac-ddb6499e8d12:4ad403a92954d6205b0c300e74fd0d67c5b371b7869f5c9a08a8de71910c4455	{"kind": "signal-analysis", "pair": "WLD", "entry": "0.431", "status": "FALLBACK_ACCEPTED", "direction": "LONG", "stop_loss": "0.4195", "dispatches": 1, "source_text": "#WLD $WLD LONG TRADE\\n\\nENTRY: 0.431\\n\\nTARGET: 0.46\\n\\nSTOPLOSS: 0.4195", "take_profits": ["0.46"], "failure_class": "codex exited with status 1", "canonical_signal": true, "management_action": null, "management_symbol": null, "source_message_id": 16160, "source_received_at": "2026-09-19T18:35:41+00:00"}	1	2026-09-19 18:36:52.690771+00	2026-09-19 18:36:41.625399+00	\N	\N	\N	\N
203b7964-0594-477e-b6a9-077785219eaf	dispatch-transition:cb58a3cb-5c50-47f5-8228-306ebca6e813:VALIDATED:SUBMITTING	{"kind": "execution-event", "reason": "none", "to_state": "SUBMITTING", "from_state": "VALIDATED", "dispatch_id": "cb58a3cb-5c50-47f5-8228-306ebca6e813"}	1	2026-09-19 18:36:53.9805+00	2026-09-19 18:36:52.194312+00	\N	\N	\N	\N
4c6cc3e1-defd-4e22-8b4c-cf3ee5bb4f94	analysis:89db9d87-9c3a-4fd8-acf7-072f0fd9ff3a:fc39fc6b73d331dcc03622520ae46929a85f3b02b61f929c67a5209f4304cee1	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "Trade still running, was up in profit and now back to entry\\n\\nI will be holding\\n\\nA daily close above 0.432 would be really bullish", "take_profits": [], "failure_class": "codex exited with status 1", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16161, "source_received_at": "2026-09-19T22:48:29+00:00"}	1	2026-09-19 22:48:59.587001+00	2026-09-19 22:48:51.974257+00	\N	\N	\N	\N
5080d151-7e53-42ae-a2b4-bb51cbb44b2f	analysis:8f6d31cf-33e0-4872-97fe-4ec6b8058aa5:6714587364fdf023d76674d255f43d159e7cf5f6f315291b5dce629aea7dbe77	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "$WLD trade update: \\n\\nThe trade hit our invalidation and right now retested at the key level as you can see in the chart, if we fail to hold 0.417 then I will be looking for long below at around 0.387", "take_profits": [], "failure_class": "codex exited with status 1", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16162, "source_received_at": "2026-09-20T14:35:03+00:00"}	1	2026-09-20 14:35:41.506566+00	2026-09-20 14:35:32.646015+00	\N	\N	\N	\N
73cd6e70-719e-4026-bf46-4471782a7ce5	analysis:1f59e9aa-f96c-4fd2-a61f-a7937dad249d:03c7783bdf47169bd4188566122662836f63b699f46f60fb53f5d0bb249cc5d6	{"kind": "signal-analysis", "pair": "none", "entry": "none", "status": "MANUAL_REVIEW", "direction": "none", "stop_loss": "none", "dispatches": 0, "source_text": "$WLD up almost 10% from our level", "take_profits": [], "failure_class": "codex exited with status 1", "canonical_signal": false, "management_action": null, "management_symbol": null, "source_message_id": 16163, "source_received_at": "2026-09-20T17:08:35+00:00"}	1	2026-09-20 17:08:47.281917+00	2026-09-20 17:08:40.039642+00	\N	\N	\N	\N
b53e8f46-dafc-4caa-b7e8-3b631ede729a	analysis:43946e25-f0fe-4914-973c-60d3afcfd3c2:a895d62692bfc5685b0294f2e8d1d5584c5228d3912d85d8ad5a0bb1c5d89187	{"kind": "signal-analysis", "pair": "BONK", "entry": "0.003015", "status": "FALLBACK_ACCEPTED", "direction": "LONG", "stop_loss": "0.002935", "dispatches": 1, "source_text": "#BONK $BONK LONG TRADE\\n\\nENTRY: 0.003015\\n\\nTARGET: 0.00333\\n\\nSTOPLOSS: 0.002935", "take_profits": ["0.00333"], "failure_class": "codex exited with status 1", "canonical_signal": true, "management_action": null, "management_symbol": null, "source_message_id": 16164, "source_received_at": "2026-09-20T17:19:10+00:00"}	1	2026-09-20 17:19:41.91491+00	2026-09-20 17:19:35.304109+00	\N	\N	\N	\N
\.


--
-- Data for Name: operator_telegram_updates; Type: TABLE DATA; Schema: public; Owner: fatty_app
--

COPY public.operator_telegram_updates (update_id, claimed_at) FROM stdin;
576281632	2026-09-09 02:00:06.753392+00
576281633	2026-09-09 17:11:46.492832+00
576281634	2026-09-09 17:11:48.559413+00
576281635	2026-09-10 01:36:44.518732+00
576281636	2026-09-11 09:31:00.635979+00
576281637	2026-09-11 10:22:30.072468+00
576281638	2026-09-11 16:37:43.857039+00
576281639	2026-09-13 09:26:58.255267+00
576281640	2026-09-13 14:28:20.658585+00
576281641	2026-09-13 14:28:25.550827+00
576281642	2026-09-15 13:38:04.661398+00
576281643	2026-09-15 13:38:17.818737+00
576281644	2026-09-15 13:38:25.496827+00
576281645	2026-09-15 13:38:34.975427+00
576281646	2026-09-15 14:59:30.899073+00
576281647	2026-09-15 14:59:35.468225+00
576281648	2026-09-15 15:04:49.887361+00
40256225	2026-09-20 00:47:58.070224+00
\.


--
-- Data for Name: orders; Type: TABLE DATA; Schema: public; Owner: fatty_app
--

COPY public.orders (id, dispatch_id, position_id, exchange, client_order_id, venue_order_id, role, state, created_at) FROM stdin;
\.


--
-- Data for Name: position_snapshots; Type: TABLE DATA; Schema: public; Owner: fatty_app
--

COPY public.position_snapshots (id, exchange, symbol, side, size, entry_price, mark_price, liquidation_price, leverage, margin_mode, unrealized_pnl, captured_at) FROM stdin;
\.


--
-- Data for Name: positions; Type: TABLE DATA; Schema: public; Owner: fatty_app
--

COPY public.positions (id, exchange, symbol, direction, quantity, protection_state, opened_at, closed_at) FROM stdin;
\.


--
-- Data for Name: protection_states; Type: TABLE DATA; Schema: public; Owner: fatty_app
--

COPY public.protection_states (id, position_id, order_ref, sl_order_id, tp_order_id, state, updated_at) FROM stdin;
\.


--
-- Data for Name: provider_reconciliation_events; Type: TABLE DATA; Schema: public; Owner: fatty_app
--

COPY public.provider_reconciliation_events (id, exchange, provider_order_id, provider_fill_id, client_order_id, symbol, side, source, quantity, price, fee, realized_pnl, state, observed_at, created_at) FROM stdin;
085fe228-bc31-5f1c-be5e-8835b4b22612	bitget	1483115597766737921	1483115597766737920	provider-bitget-ec118a2429b8e703bd335458	WLDUSDT	SELL	SYSTEM_LIQUIDATION	91	0.3841	0.02097366	-1.17999853	reconciled	2026-09-13 19:37:14.139+00	2026-09-14 04:51:53.475472+00
d71f67db-d693-54a0-b4a1-d1eaa0e56dc4	bitget	1476230982368116738	1476230982409117698	provider-bitget-2018f9c6cd09ad399fd99176	ENAUSDT	SELL	SYSTEM_LIQUIDATION	39	0.14741	0.00344939	-0.19344	reconciled	2026-08-25 19:40:13.928+00	2026-09-14 04:51:53.53022+00
65309958-88cd-51cb-98d3-7dc44ce99130	bitget	1475834704450699268	1475834704491712518	provider-bitget-a451acf048bd6423ed8b966a	FARTCOINUSDT	SELL	SYSTEM_LIQUIDATION	5.6	0.1773	0.00059572	-0.03528	reconciled	2026-08-24 17:25:33.91+00	2026-09-14 04:51:53.578201+00
02a66774-753b-50b8-ab83-83daf9489ee5	bitget	1475176827503394818	1475176827540209668	provider-bitget-f749f5f1e4a72aecabb3ea01	ORDIUSDT	SELL	SYSTEM_LIQUIDATION	1.41	4.006	0.00338907	-0.33276	reconciled	2026-08-22 21:51:23.822+00	2026-09-14 04:51:53.626048+00
\.


--
-- Data for Name: reconciliation_state; Type: TABLE DATA; Schema: public; Owner: fatty_app
--

COPY public.reconciliation_state (scope, last_run_at, last_success_at, mismatch_count, updated_at) FROM stdin;
\.


--
-- Data for Name: schema_migrations; Type: TABLE DATA; Schema: public; Owner: fatty_app
--

COPY public.schema_migrations (version, applied_at) FROM stdin;
1	2026-09-05 03:01:41.983846+00
2	2026-09-05 16:59:35.452109+00
3	2026-09-05 16:59:35.452109+00
4	2026-09-05 16:59:35.452109+00
5	2026-09-05 18:19:30.380428+00
6	2026-09-07 15:56:29.619539+00
7	2026-09-07 15:56:29.619539+00
8	2026-09-08 16:54:37.010615+00
9	2026-09-08 16:59:56.015014+00
10	2026-09-08 17:10:12.191584+00
11	2026-09-14 04:48:08.924993+00
12	2026-09-14 04:48:08.924993+00
13	2026-09-17 09:10:17.410483+00
\.


--
-- Data for Name: source_management_provider_intents; Type: TABLE DATA; Schema: public; Owner: fatty_app
--

COPY public.source_management_provider_intents (management_update_id, client_order_id, created_at) FROM stdin;
\.


--
-- Data for Name: source_management_updates; Type: TABLE DATA; Schema: public; Owner: fatty_app
--

COPY public.source_management_updates (id, source_message_id, revision, symbol, action, state, claimed_by, claimed_at, created_at, updated_at) FROM stdin;
84c95645-92b9-426b-91d2-dbd38682a8f3	66ecc6fc-8612-45f2-adb4-c8f575d87681	16b5efa897d806eae7d25a9f4a501a66f9569c727bccb5f29b0e88d93b075373	XPLUSDT	SL_TO_ENTRY	reconciled	source-management	2026-09-09 17:56:16.98767+00	2026-09-08 22:57:30.550085+00	2026-09-09 17:56:17.397065+00
51ad0641-9e02-41b3-b171-b7c5974a7032	b2d7e19b-590d-4c69-b16f-b75c6e1d8568	320d24db49ec6d9d3ad2c764535ebc4425aa5c1c8b65b2b0b58e7c4d3d197961	INJUSDT	SL_TO_ENTRY	reconciled	source-management	2026-09-11 20:08:13.735972+00	2026-09-11 20:07:49.758675+00	2026-09-11 20:08:14.621057+00
72984bda-8fb4-4882-9ec5-d05e46e42991	b0444f30-8d9d-4f80-b10b-61d1652c1bb6	71bbfc9a47f6ab2112771d5c16ebe498336838fd62e3537be57dbf0cd1530f6b	SLUSDT	SL_TO_ENTRY	reconciled	source-management	2026-09-12 11:58:37.206091+00	2026-09-12 11:58:23.929006+00	2026-09-12 11:58:37.847814+00
77545328-e39f-4d2b-98ae-53a7ab7e129c	0656c4cf-fa1c-4510-862e-a41c636adfa8	400abe1e9fe01369e67605d301b205d09e61ded77ce08105b52ac3f5e2c6e508	PENDLEUSDT	SL_TO_ENTRY	failed	source-management	2026-09-18 01:52:40.931208+00	2026-09-18 01:52:31.740788+00	2026-09-18 01:52:41.840182+00
\.


--
-- Data for Name: telegram_messages; Type: TABLE DATA; Schema: public; Owner: fatty_app
--

COPY public.telegram_messages (id, channel_id, message_id, revision_hash, received_at, raw_text, intake_state, has_media, media_path, media_sha256, media_mime_type, media_size_bytes) FROM stdin;
e182eb56-8b5b-4faa-911d-2a8594eda992	-1001252615519	16091	1e239cbc7ec29455056f612b91c718bdbc5f3f53ff72027936506e239754f5e8	2026-09-05 12:27:39+00	book profits on $APE & $PUMP sl hit	ANALYZED	f	\N	\N	\N	\N
1318f002-3144-48c6-a7d9-23a6222b93e5	-1001252615519	16093	6041f2644e979f9c5307f5cdc3ae1361cf48c1dab1980ed2c4039733ba1eab57	2026-09-05 16:47:17+00	$BTC possible weekend move\n\nRetest around Fridays lows then back to 81k as we are trapped between the liquidity around 83k and the one below at 78k	ANALYZED	f	\N	\N	\N	\N
0619ca73-a958-411e-82da-47da9656a857	-1001252615519	16096	9a40620fb725720b6387575449c302fdbcf4e6c91cb9dda438fcc0a4256c7ee3	2026-09-06 13:55:49+00	Goodmorning ❤️\n\n$AIXBT showing no movement so will be closing it here for small profit	ANALYZED	f	\N	\N	\N	\N
b9f2f84c-7384-4f27-bb09-95f8a5fa78e9	-1001252615519	16110	6bf6091c5994875245c5b4da64ef4a4618391a0b572db912ca2abe3f5b15e994	2026-09-09 12:03:21+00	$BTC going perfect so far🔥\n\nhttps://x.com/learnernoearner/status/2097656935839383803?s=46	ANALYZED	f	\N	\N	\N	\N
c8533589-43a0-460e-88f7-09601f81d252	-1001252615519	16090	ed173cc7907464c767074ec05e2579b5bf33fc60f9a726663936d08b51793297	2026-09-04 21:02:33+00	#PUMP $PUMP LONG TRADE\n\nENTRY: 0.00427\n\nTARGETS: 0.004438 - 0.004915\n\nSTOPLOSS: 0.00416	ANALYZED	f	\N	\N	\N	\N
5714ab4a-772b-420e-84b4-3759d8ccdac3	-1001252615519	16097	d6e75e6b2802d2b7425a2f6687d092b91c5a80c83c63531474003d33a9e5c9e2	2026-09-06 15:43:06+00	#SUSHI $SUSHI LONG TRADE\n\nENTRY: 0.2512\n\nTARGET: 0.2835\n\nSTOPLOSS: 0.2428	ANALYZED	f	\N	\N	\N	\N
56351963-d5fe-44b6-9ee7-02bcf16f0e81	-1001252615519	16098	d1b167e2d495786b90e2f3e117d73a5cded811658540d10aee84ec070f83f798	2026-09-06 16:30:18+00	$SUSHI more than 1R up, tp1 booked	ANALYZED	f	\N	\N	\N	\N
d5ebf52d-a56b-4d9c-99a8-a00024969e85	-1001252615519	16099	1718d262b4c181aad6124e1df9d161b886f4c7740cb46a700baa6557b8a5c199	2026-09-07 12:53:40+00	Goodmorning❤️\n\nhttps://x.com/learnernoearner/status/2096944824897134706?s=46	ANALYZED	f	\N	\N	\N	\N
a81098c7-eca3-4861-a937-824cf2ee8cf9	-1001252615519	16100	31ad0cd230efe4e6b0c18e274ea9a58ba6794f5ecad6d4cb89715908e7851ff6	2026-09-07 13:54:27+00	#NOT $NOT LONG TRADE\n\nENTRY: 0.0004715\n\nTARGET: 0.00058\n\nSTOPLOSS: 0.000458	ANALYZED	f	\N	\N	\N	\N
d3b9a913-462f-447b-bc17-577624756abe	-1001252615519	16101	a306301908d23e20dad35d4c0fe9d7f490c8d50b3df60dc3854ef015adf1ce97	2026-09-08 13:35:10+00	$BTC weekend plan update:\n\nstill bullish here as we still above support, my invalidation will be below 77.7k	ANALYZED	f	\N	\N	\N	\N
5776e5d7-ee6d-4f90-b65f-c666a131985a	-1001252615519	16102	72a1ea919d92a7d03c348f1bd0edae5b8f2db9e6ad4f760caf74799d82aedf12	2026-09-08 13:59:18+00	Sl hit on $NOT	ANALYZED	f	\N	\N	\N	\N
854d4686-8f42-48ef-9997-a174886c7718	-1001252615519	16103	3f8ed5920fbd14b5b6afd0c1137ebc2797aa5634d286747c6995e2e8b20f1e9d	2026-09-08 14:05:12+00	#WLD $WLD LONG TRADE\n\nENTRY: 0.47\n\nTARGET: 0.51\n\nSTOPLOSS: 0.4562	ANALYZED	f	\N	\N	\N	\N
bcb72f30-0ed8-4e6d-9d11-e816243239f8	-1001252615519	16105	b5aaad5a4824f06034aa26ddbbba4bf980573669cd337be77467ffa53d9f025f	2026-09-08 15:43:23+00	$WLD TP1 booked here at 2R	ANALYZED	f	\N	\N	\N	\N
6ace015d-1783-4761-93b8-c7813b28cb4c	-1001252615519	16106	ce028c03b5b7d7e54c1c09d16b2e937624a13126cb1e95ba672fb0475d11a223	2026-09-08 15:43:29+00	$WLD TP1 booked here at 2R	ANALYZED	f	\N	\N	\N	\N
cd4f924e-26b5-44a5-9458-b55d98cd2fa5	-1001252615519	16107	16cf7ebc3bf041f9d21f79acbe944f89e8e089c3244dafc4ebbe43420e8e2f8f	2026-09-08 16:45:39+00	#FLOKI $FLOKI LONG TRADE\n\nENTRY: 0.02665\n\nTARGET: 0.0304\n\nSTOPLOSS: 0.02587	ANALYZED	f	\N	\N	\N	\N
679b2eb6-3775-4146-be11-dbd14a862014	-1001252615519	16108	d29b9382cb8adef607fa002da0dfc9dbfa687f6187bc878acc5a027f1829f5cb	2026-09-08 20:48:12+00	#XPL $XPL LONG TRADE\n\nENTRY: 0.098 - 0.096\n\nTARGET: 0.117\n\nStoploss: 0.09475	ANALYZED	f	\N	\N	\N	\N
66ecc6fc-8612-45f2-adb4-c8f575d87681	-1001252615519	16109	16b5efa897d806eae7d25a9f4a501a66f9569c727bccb5f29b0e88d93b075373	2026-09-08 22:56:50+00	$XPL sl to entry	ANALYZED	f	\N	\N	\N	\N
81fe0d12-d6d3-46d3-ac1b-83609ad149e6	-1001252615519	16111	e94dec6e9971b34e9b5566a5b16e783d418fbd2fbfe0853ff2ab6c817092212a	2026-09-09 13:47:52+00	#WLD $WLD LONG TRADE\n\nENTRY: 0.4495\n\nTARGET: 0.491\n\nSTOPLOSS: 0.4345	ANALYZED	f	\N	\N	\N	\N
a27542cb-42bd-4f7e-88ab-843b5e11f4c2	-1001252615519	16112	f13d0e71e5d9b99299fb1352c0ce179e1eec41d27612f2e1212b7b828c17ce7c	2026-09-09 17:46:12+00	$FLOKI and $wld sl smashed	ANALYZED	f	\N	\N	\N	\N
bd21219f-6703-40ab-b0c9-b3bbaeee97a7	-1001252615519	16113	0a8ce390cef3a536a447e1d4b1c05078df18c7e4eae83bfedac3577b066f0997	2026-09-10 12:58:55+00	Goodmorning ❤️\n\nWaiting for the perfect opportunity to reveal itself	ANALYZED	f	\N	\N	\N	\N
b797b455-8532-4b9a-abba-e84361a3b4f0	-1001252615519	16114	b5f80844c2d2e2408d21580f07be8c4d8de0a227ce7b99232fcb7f81e901283f	2026-09-10 14:06:40+00	$BTC LTF\n\nWe are back to the range lows here, i would like another small drop to around 76 - 76.5k before looking for longs	ANALYZED	f	\N	\N	\N	\N
93031ac9-89d0-4107-8685-0395dafc9d64	-1001252615519	16115	a405cd0f2ffd422e240616f5543e64ba03d5cfd9369896908db7c7be985766b9	2026-09-10 17:25:35+00	#GRASS $GRASS LONG TRADE\n\nENTRY: 0.3505\n\nTARGET: 0.414\n\nSTOPLOSS: 0.3382	ANALYZED	f	\N	\N	\N	\N
a0000000-0000-0000-0000-000000000001	134570	999999999	0                                                               	2026-09-11 09:03:08.342879+00	MANUAL SIGNAL: GRASSUSDT LONG @ 0.34690 SL 0.34400 TP1 0.34960 TP2 0.35290	ANALYZED	f	\N	\N	\N	\N
c1fbe501-d8d1-44b3-bc33-cc6dba866a0c	-1001252615519	16116	57c2b7299c3b2e931a8330d93104c091f6875b69e1dca37b03837004c3737fdc	2026-09-11 11:17:32+00	goodmorning❤️\n\n $BTC hit our long area and pumped as expected	ANALYZED	f	\N	\N	\N	\N
2b4c7fc2-35f3-433c-bcac-0a1f105247e0	-1001252615519	16117	cd58651da321746e9036a1b1317164a1ed339d898b835ef760edca84f0797ab9	2026-09-11 13:00:33+00	$BTC fully done	ANALYZED	f	\N	\N	\N	\N
5a8f02af-6c93-412c-8121-b0010d83ae3f	-1001252615519	16118	c2fa10dd2c1f3c4951fb6c5cf9d692e24991b867228117591cc44ee86de2b3e3	2026-09-11 14:18:40+00	$GRASS invalid	ANALYZED	f	\N	\N	\N	\N
0e1d31b9-fa8a-42c5-bf9c-d89d6924bfe7	-1001252615519	16119	473ee794a1674e10821d90b6a5fc8fff2d0e7e8070c750d78c5c6870e189c4bf	2026-09-11 14:18:52+00	$ETH done	ANALYZED	f	\N	\N	\N	\N
966d30d5-26fd-48c7-adb9-ba8dabffdbe3	-1001252615519	16120	4dd1c1b34fe2310ae1fb634d8cd6a07e1999a5e4312c4c071b5451197004a418	2026-09-11 18:27:28+00	#INJ $INJ LONG TRADE\n\nENTRY: 5.835\n\nTARGET: 6.72\n\nSTOPLOSS: 5.66	ANALYZED	f	\N	\N	\N	\N
b2d7e19b-590d-4c69-b16f-b75c6e1d8568	-1001252615519	16121	320d24db49ec6d9d3ad2c764535ebc4425aa5c1c8b65b2b0b58e7c4d3d197961	2026-09-11 20:07:43+00	$INJ sl to entry	ANALYZED	f	\N	\N	\N	\N
561dc43c-ecea-4701-8276-633ea8d47f00	-1001252615519	16122	81d987b81e65a91aa9abdc4726f6a09e83cac4cbe45514eec2baae970c3a1068	2026-09-12 11:40:47+00	#ETHFI $ETHFI LONG TRADE\n\nENTRY: 0.751 - 0.73\n\nTARGET: 0.87\n\nSTOPLOSS: 0.718	ANALYZED	f	\N	\N	\N	\N
b0444f30-8d9d-4f80-b10b-61d1652c1bb6	-1001252615519	16123	71bbfc9a47f6ab2112771d5c16ebe498336838fd62e3537be57dbf0cd1530f6b	2026-09-12 11:57:35+00	sl to entry $ETHFI	ANALYZED	f	\N	\N	\N	\N
ffafa787-e92a-47f7-adbb-6379dd0045aa	-1001252615519	16124	23c1c4c4a60a7ce4514b67b894d4c468aadc9718efe9466e0b2c980640731637	2026-09-12 17:31:01+00	#EUL $EUL LONG TRADE\n\nENTRY: 1.268\n\nTARGET: 1.47\n\nSTOPLOSS: 1.2365	ANALYZED	f	\N	\N	\N	\N
214b7e8c-b624-4772-bcb7-9f704d46748a	-1001252615519	16125	9203f5d562b4a9fa53eb2880d7a554edab634c5c1743142bbc75caddea8543f5	2026-09-12 18:10:54+00	book some profits on $EUL	ANALYZED	f	\N	\N	\N	\N
e7f89b7d-e8da-43c3-872a-d932564c0419	-1001252615519	16126	311a40ff51b8abb56a5cfd11ee6734814b5cb2b19ddc4b52322f8b2715dd2159	2026-09-13 03:48:04+00	#PONS $PONS LONG TRADE\n\nENTRY: 0.584\n\nTARGET: 0.747\n\nSTOPLOSS: 0.564	ANALYZED	f	\N	\N	\N	\N
c8249cd2-7024-4c3b-9246-ecaa9545be14	-1001252615519	16127	9a21f862e4569e051cef61a15cdf5ebc031b86ffe91e7e86a4f8e02c22d58ba8	2026-09-13 13:42:45+00	$PONS did 3% then later dumped to sl	ANALYZED	f	\N	\N	\N	\N
ac5ebb01-8a3b-40af-bb84-c1145d204598	-1001252615519	16128	3886302802ed718b40cec19aa66a88b18552b7970089e48fdfa08275e0bd5f77	2026-09-13 14:27:48+00	#WLD $WLD LONG TRADE\n\nENTRY: 0.396\n\nTARGET: 0.427\n\nSTOPLOSS: 0.388	ANALYZED	f	\N	\N	\N	\N
5a854c73-284b-47f9-85a2-a09d45f26ce6	-1001252615519	16129	73a2cb918d067c4f7d41253e6a4d0329f99735980457dea72825747bfbbe9c8f	2026-09-13 15:35:25+00	Goodmorning❤️\n\nhttps://x.com/learnernoearner/status/2099159961204838555?s=46	ANALYZED	f	\N	\N	\N	\N
db3099a9-cc77-4bc7-8b56-01618057f247	-1001252615519	16130	28c2c1a24d6ace5b6a9d7bcbcdc574c4bb080c9cab2087838fef663f7964380d	2026-09-14 12:57:23+00	$WLD sl hit	ANALYZED	f	\N	\N	\N	\N
8d6e5815-2c41-48b4-aab3-ee74b32a9747	-1001252615519	16131	10bb3ce408ffe9b85ec092eff19b3cf08e031c60ec5033ea94935519f310b908	2026-09-14 13:00:21+00	Goodmorning❤️\n\nNow that the weekend is over lets get back to work, will start with doing $BTC first then we trade\n\nMissed sharing $CL trade yesterday night but i feel as sleep😭\n\nhttps://x.com/learnernoearner/status/2099483352935936211?s=46	ANALYZED	f	\N	\N	\N	\N
e1ccfe43-e8f8-40f6-9bbf-f13ccbfac1f7	-1001252615519	16132	687baa6a1d43a0a1e12dabbaad220d6b6dec7e0dc160cbaa0cccf46d48c0da3d	2026-09-14 13:56:04+00	$BTC todays move\n\nhttps://x.com/learnernoearner/status/2099497373311369385?s=46	ANALYZED	f	\N	\N	\N	\N
43de9ca9-8a25-4049-8521-8952f18d5dcf	-1001252615519	16145	f07f8884064ea8f4aa06300d27487fe87111357f4522c24cb55354fdc69fd17b	2026-09-16 16:50:35+00	$PUMP hold above the box and we will see 0.004+ quickly	ANALYZED	f	\N	\N	\N	\N
f2c137d9-871a-426b-92c1-9094c46d6dbb	-1001252615519	16133	1475a9a58b03a4864980b730227894efff93e6ba2d29a3a95129b0fde6bf79c6	2026-09-14 14:22:28+00	Shorting $WLD here around 0.385\n\nStoploss: 0.3938	ANALYZED	f	\N	\N	\N	\N
d796ea2d-fa83-42dc-b607-9f6b5c276ede	-1001252615519	16134	20f8143977a56b0e052774cc12d4bb8509a313937c705817d78839126f2223fa	2026-09-14 23:12:14+00	$WLD invalid	ANALYZED	f	\N	\N	\N	\N
fe50b07e-1777-4ef4-a695-de6912c898c4	-1001252615519	16135	6f0cec363ea77c768d30e80d131a1e027c7e40e01a057cafd24d4dcf74d9233a	2026-09-15 01:29:10+00	$BTC todays move played out as expected\n\nhttps://x.com/learnernoearner/status/2099671761348448468?s=46	ANALYZED	f	\N	\N	\N	\N
9ac2cf12-42f4-4941-9600-b348488a8c8f	-1001252615519	16136	ffee46a5047c85dd10c00a9d6756a23e05a0dbba39266c9376b73751a866e6e0	2026-09-15 12:20:37+00	$BTC dropped 3%+ from our level and its almost back to the 76.3k support if we dont hold this level then 75k will be the key level between us and the goblin town\n\nAnyways will let this setup play out and analyse $ETH today\n\nhttps://x.com/learnernoearner/status/2099835727265542447?s=46	ANALYZED	f	\N	\N	\N	\N
1300390c-5300-49ca-9ce4-ade0dcf576e4	-1001252615519	16137	8c08b2e093ab9511e79dedc1ba02b0b1a479ec24ac3e94996c5c8da59e1b6436	2026-09-15 12:58:42+00	$ETH todays move	ANALYZED	f	\N	\N	\N	\N
d5fab280-0de5-4050-a55d-c36ded76cd58	-1001252615519	16138	959de2ad5273b8f419be00253738cdcdf16498efb34cd8bbdd516a8f23209770	2026-09-15 14:09:19+00	$SNDK LONG TRADE\n\nENTRY: 1560\n\nTARGET: 1795\n\nSTOPLOSS: 1535	ANALYZED	f	\N	\N	\N	\N
83a33e8c-82fc-4fc1-91d9-c76d6dc16c2e	-1001252615519	16139	3625aecd3b3702920feb61ac13af03dd39802397132275dc2221f1f57b3cbd43	2026-09-15 14:54:30+00	$BTC done ✅ \n\nSee yall tomorrow with a new update	ANALYZED	f	\N	\N	\N	\N
325c2c05-2255-4a6a-8e46-791a109cfaba	-1001252615519	16140	62ddabd89408a1de6cf7a290062afbffd3cc14ddc68095fd243858c7d4f877d1	2026-09-15 14:55:03+00	$ETH done too \n\nCalled both dump on $ETH & $BTC	ANALYZED	f	\N	\N	\N	\N
6256a540-bf35-4684-ada7-de973197a068	-1001252615519	16141	6c1dd959c6201637b0c95ffa36c3324fb714a169277ef24bdd78e14451826a54	2026-09-15 16:03:14+00	SNDK hit sl	ANALYZED	f	\N	\N	\N	\N
c9cfc94a-d500-42c8-b12f-16bc9172c129	-1001252615519	16142	9e3b29dc3e0bcef60779c4152cd313676c69c065307fb474a9d9974a851bcea9	2026-09-15 16:17:23+00	#TAO $TAO LONG TRADE\n\nENTRY: 224.5\n\nTARGET: 236.9\n\nSTOPLOSS: 218.55	ANALYZED	f	\N	\N	\N	\N
7ecb6599-d9de-4afb-9649-228d2572c53f	-1001252615519	16143	5caf848809a02cb3c07843aded35d6bd88e6a28ebc575257b935bb8697b70002	2026-09-16 12:51:08+00	$TAO sl hit	ANALYZED	f	\N	\N	\N	\N
28d1be3a-92a6-4144-a157-899d943b7cf3	-1001252615519	16144	1fac41fab67d6f990f690e315fd64057c5c5a3f822c4228164fc6c3708b75412	2026-09-16 14:13:37+00	$BTC todays move\n\nBids placed around 74k area	ANALYZED	f	\N	\N	\N	\N
0ccd450e-a631-414c-b2fc-97b463788489	-1001252615519	16146	45c6353b18d7591d78d30f690730dc25544655daf4ebb2d38079ab72fffe7dba	2026-09-16 18:59:10+00	$BTC shorted based on our plan\n\nhttps://x.com/learnernoearner/status/2100298326767817025?s=46	ANALYZED	f	\N	\N	\N	\N
663fc8e1-216d-4852-8968-06419dc0bfab	-1001252615519	16147	d185b60ab8bad5937c2924f9f1c168b948cf07a7c123b2a959f9595d45e5c4e4	2026-09-16 19:04:37+00	Main plan was to long lower but did a small short scalp	ANALYZED	f	\N	\N	\N	\N
d84df5a3-6bab-4204-88a1-2ab185a14748	-1001252615519	16149	b3ec6b59939f6ff25ffaa5add5d3a371ecdc3e35e3c8d61467a4c1ba08ccd4a1	2026-09-17 09:35:53+00		ANALYZED	t	\N	\N	\N	\N
7e4ee4f4-ff8b-4019-8e10-c43ea28d279b	-1001252615519	16150	42ebe02ecf5b6c2054e5f29cd11a364d0453bd32485b5747f460c8a59bd84392	2026-09-17 09:36:05+00	Goodmorning $PUMP ❤️\n\nhttps://x.com/learnernoearner/status/2100518967836070266?s=46	ANALYZED	t	\N	\N	\N	\N
c3ef821d-fadb-4f27-83d1-fa94f547e6eb	-1001252615519	16151	ca992d86cec8eac7b3500f8958087563f02d222e57a52bd821dd0309eb521ef8	2026-09-17 15:51:39+00	$PUMP 15% in a day, full tp done	ANALYZED	t	\N	\N	\N	\N
7328ff35-0eb0-4cce-95bb-f2ee00aae80f	-1001252615519	16152	84f7c9437aaca5f320aa9c4f9ebd02870c82fdf3538d8572c0f0eb5e6b04634b	2026-09-17 20:24:07+00	#PENDLE $PENDLE LONG TRADE\n\nENTRY: 2.32\n\nTARGET: 3.22\n\nSTOPLOSS: 2.2465	ANALYZED	t	\N	\N	\N	\N
0656c4cf-fa1c-4510-862e-a41c636adfa8	-1001252615519	16153	400abe1e9fe01369e67605d301b205d09e61ded77ce08105b52ac3f5e2c6e508	2026-09-18 01:52:30+00	$PENDLE sl to entry	ANALYZED	f	\N	\N	\N	\N
d10cddfa-4451-4112-a08d-a2834d83fd2e	-1001252615519	16154	d7e671ef38a9365f738551efb9f7b211c896e4906318369c754459e6af8725b9	2026-09-18 02:07:23+00	$PENDLE TP1 here	ANALYZED	f	\N	\N	\N	\N
d17659ad-c1ee-4c2d-b49f-aa894246ddf3	-1001252615519	16155	a888af4db7c17b1ab1776498824f297f1c2fe03c5dced816436dc4df23710259	2026-09-18 02:23:12+00	#SAGA $SAGA LONG TRADE\n\nENTRY: 0.0221 - 0.02155\n\nTARGET: 0.036\n\nSTOPLOSS: 0.02112	ANALYZED	t	\N	\N	\N	\N
415aeaa7-7f63-4786-b610-a7b19b0e8552	-1001252615519	16156	9b652e19f0ad17b600a27b002e2bd6f960d691bf529dfc9d38a4ae45508e6423	2026-09-18 12:54:59+00	$PENDLE 16% fully closed here\n\n$SAGA pumped 1R after filling both entries then dumped	ANALYZED	f	\N	\N	\N	\N
be97cbb1-e7f3-484e-9aa6-aa1ae6e4d29e	-1001252615519	16157	79ae4ee93247de4aa00ad0def3bc4d4de5b38dac74d06f17a5bb03989214a0d8	2026-09-18 13:53:43+00	$BTC short @ 80400\n\nSl above 81280	ANALYZED	t	\N	\N	\N	\N
5766463d-3931-4a12-8c60-852c775a32cf	-1001252615519	16158	8033fabe20522b65701c43a408e2cfab84b7dfb91186f3ad104ea28feb863520	2026-09-18 16:34:43+00	closing my short here at 81k\n\nWas wrong on this one	ANALYZED	f	\N	\N	\N	\N
c30f710a-0835-47c2-8b25-1e493807a68b	-1001252615519	16159	30a8903f201a52d9cc4a02b1a2e194cca8c00120f393450002c9ceb943304532	2026-09-19 13:42:32+00	Goodmorning❤️\n\n$BTC bounced at the midrange from our HTF update, as we mentioned that a long would be built there and now we are close to resistance again \n\nEverything is written in the update no need to explain again	ANALYZED	t	\N	\N	\N	\N
f0e2b989-749b-4fc0-a0ac-ddb6499e8d12	-1001252615519	16160	4ad403a92954d6205b0c300e74fd0d67c5b371b7869f5c9a08a8de71910c4455	2026-09-19 18:35:41+00	#WLD $WLD LONG TRADE\n\nENTRY: 0.431\n\nTARGET: 0.46\n\nSTOPLOSS: 0.4195	ANALYZED	t	\N	\N	\N	\N
89db9d87-9c3a-4fd8-acf7-072f0fd9ff3a	-1001252615519	16161	fc39fc6b73d331dcc03622520ae46929a85f3b02b61f929c67a5209f4304cee1	2026-09-19 22:48:29+00	Trade still running, was up in profit and now back to entry\n\nI will be holding\n\nA daily close above 0.432 would be really bullish	ANALYZED	f	\N	\N	\N	\N
8f6d31cf-33e0-4872-97fe-4ec6b8058aa5	-1001252615519	16162	6714587364fdf023d76674d255f43d159e7cf5f6f315291b5dce629aea7dbe77	2026-09-20 14:35:03+00	$WLD trade update: \n\nThe trade hit our invalidation and right now retested at the key level as you can see in the chart, if we fail to hold 0.417 then I will be looking for long below at around 0.387	ANALYZED	t	\N	\N	\N	\N
1f59e9aa-f96c-4fd2-a61f-a7937dad249d	-1001252615519	16163	03c7783bdf47169bd4188566122662836f63b699f46f60fb53f5d0bb249cc5d6	2026-09-20 17:08:35+00	$WLD up almost 10% from our level	ANALYZED	t	\N	\N	\N	\N
43946e25-f0fe-4914-973c-60d3afcfd3c2	-1001252615519	16164	a895d62692bfc5685b0294f2e8d1d5584c5228d3912d85d8ad5a0bb1c5d89187	2026-09-20 17:19:10+00	#BONK $BONK LONG TRADE\n\nENTRY: 0.003015\n\nTARGET: 0.00333\n\nSTOPLOSS: 0.002935	ANALYZED	t	\N	\N	\N	\N
\.


--
-- Data for Name: venue_kill_switches; Type: TABLE DATA; Schema: public; Owner: fatty_app
--

COPY public.venue_kill_switches (scope, active, reason, latched_at, updated_at) FROM stdin;
bitget	f	released:protection-alert-only-policy	2026-09-05 16:59:44.788365+00	2026-09-15 14:27:25.913622+00
\.


--
-- Name: balance_snapshots balance_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.balance_snapshots
    ADD CONSTRAINT balance_snapshots_pkey PRIMARY KEY (id);


--
-- Name: bitget_protection_capabilities bitget_protection_capabilities_pkey; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.bitget_protection_capabilities
    ADD CONSTRAINT bitget_protection_capabilities_pkey PRIMARY KEY (exchange, environment, symbol);


--
-- Name: canary_entry_reservations canary_entry_reservations_pkey; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.canary_entry_reservations
    ADD CONSTRAINT canary_entry_reservations_pkey PRIMARY KEY (dispatch_id);


--
-- Name: canonical_signals canonical_signals_pkey; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.canonical_signals
    ADD CONSTRAINT canonical_signals_pkey PRIMARY KEY (id);


--
-- Name: dispatch_transitions dispatch_transitions_pkey; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.dispatch_transitions
    ADD CONSTRAINT dispatch_transitions_pkey PRIMARY KEY (id);


--
-- Name: dispatches dispatches_pkey; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.dispatches
    ADD CONSTRAINT dispatches_pkey PRIMARY KEY (id);


--
-- Name: dispatches dispatches_source_type_source_id_revision_exchange_key; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.dispatches
    ADD CONSTRAINT dispatches_source_type_source_id_revision_exchange_key UNIQUE (source_type, source_id, revision, exchange);


--
-- Name: fallback_protection fallback_protection_pkey; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.fallback_protection
    ADD CONSTRAINT fallback_protection_pkey PRIMARY KEY (id);


--
-- Name: fills fills_exchange_provider_fill_id_key; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.fills
    ADD CONSTRAINT fills_exchange_provider_fill_id_key UNIQUE (exchange, provider_fill_id);


--
-- Name: fills fills_pkey; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.fills
    ADD CONSTRAINT fills_pkey PRIMARY KEY (id);


--
-- Name: intent_reconciliation_audit intent_reconciliation_audit_exchange_client_order_id_resolv_key; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.intent_reconciliation_audit
    ADD CONSTRAINT intent_reconciliation_audit_exchange_client_order_id_resolv_key UNIQUE (exchange, client_order_id, resolved_state);


--
-- Name: intent_reconciliation_audit intent_reconciliation_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.intent_reconciliation_audit
    ADD CONSTRAINT intent_reconciliation_audit_pkey PRIMARY KEY (id);


--
-- Name: live_order_intents live_order_intents_exchange_client_order_id_key; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.live_order_intents
    ADD CONSTRAINT live_order_intents_exchange_client_order_id_key UNIQUE (exchange, client_order_id);


--
-- Name: live_order_intents live_order_intents_exchange_provider_order_id_key; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.live_order_intents
    ADD CONSTRAINT live_order_intents_exchange_provider_order_id_key UNIQUE (exchange, provider_order_id);


--
-- Name: live_order_intents live_order_intents_pkey; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.live_order_intents
    ADD CONSTRAINT live_order_intents_pkey PRIMARY KEY (id);


--
-- Name: notifications_outbox notifications_outbox_dedup_key_key; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.notifications_outbox
    ADD CONSTRAINT notifications_outbox_dedup_key_key UNIQUE (dedup_key);


--
-- Name: notifications_outbox notifications_outbox_pkey; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.notifications_outbox
    ADD CONSTRAINT notifications_outbox_pkey PRIMARY KEY (id);


--
-- Name: operator_telegram_updates operator_telegram_updates_pkey; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.operator_telegram_updates
    ADD CONSTRAINT operator_telegram_updates_pkey PRIMARY KEY (update_id);


--
-- Name: orders orders_exchange_client_order_id_key; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_exchange_client_order_id_key UNIQUE (exchange, client_order_id);


--
-- Name: orders orders_exchange_venue_order_id_key; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_exchange_venue_order_id_key UNIQUE (exchange, venue_order_id);


--
-- Name: orders orders_pkey; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_pkey PRIMARY KEY (id);


--
-- Name: position_snapshots position_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.position_snapshots
    ADD CONSTRAINT position_snapshots_pkey PRIMARY KEY (id);


--
-- Name: positions positions_pkey; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.positions
    ADD CONSTRAINT positions_pkey PRIMARY KEY (id);


--
-- Name: protection_states protection_states_pkey; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.protection_states
    ADD CONSTRAINT protection_states_pkey PRIMARY KEY (id);


--
-- Name: protection_states protection_states_position_id_key; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.protection_states
    ADD CONSTRAINT protection_states_position_id_key UNIQUE (position_id);


--
-- Name: provider_reconciliation_events provider_reconciliation_events_exchange_provider_fill_id_key; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.provider_reconciliation_events
    ADD CONSTRAINT provider_reconciliation_events_exchange_provider_fill_id_key UNIQUE (exchange, provider_fill_id);


--
-- Name: provider_reconciliation_events provider_reconciliation_events_pkey; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.provider_reconciliation_events
    ADD CONSTRAINT provider_reconciliation_events_pkey PRIMARY KEY (id);


--
-- Name: reconciliation_state reconciliation_state_pkey; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.reconciliation_state
    ADD CONSTRAINT reconciliation_state_pkey PRIMARY KEY (scope);


--
-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (version);


--
-- Name: source_management_provider_intents source_management_provider_intents_pkey; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.source_management_provider_intents
    ADD CONSTRAINT source_management_provider_intents_pkey PRIMARY KEY (management_update_id, client_order_id);


--
-- Name: source_management_updates source_management_updates_pkey; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.source_management_updates
    ADD CONSTRAINT source_management_updates_pkey PRIMARY KEY (id);


--
-- Name: source_management_updates source_management_updates_source_message_id_revision_symbol_key; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.source_management_updates
    ADD CONSTRAINT source_management_updates_source_message_id_revision_symbol_key UNIQUE (source_message_id, revision, symbol, action);


--
-- Name: telegram_messages telegram_messages_channel_id_message_id_revision_hash_key; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.telegram_messages
    ADD CONSTRAINT telegram_messages_channel_id_message_id_revision_hash_key UNIQUE (channel_id, message_id, revision_hash);


--
-- Name: telegram_messages telegram_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.telegram_messages
    ADD CONSTRAINT telegram_messages_pkey PRIMARY KEY (id);


--
-- Name: venue_kill_switches venue_kill_switches_pkey; Type: CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.venue_kill_switches
    ADD CONSTRAINT venue_kill_switches_pkey PRIMARY KEY (scope);


--
-- Name: balance_snapshots_exchange_time; Type: INDEX; Schema: public; Owner: fatty_app
--

CREATE INDEX balance_snapshots_exchange_time ON public.balance_snapshots USING btree (exchange, captured_at);


--
-- Name: bitget_protection_capabilities_symbol; Type: INDEX; Schema: public; Owner: fatty_app
--

CREATE INDEX bitget_protection_capabilities_symbol ON public.bitget_protection_capabilities USING btree (exchange, environment, symbol);


--
-- Name: canary_entry_reservations_exchange; Type: INDEX; Schema: public; Owner: fatty_app
--

CREATE INDEX canary_entry_reservations_exchange ON public.canary_entry_reservations USING btree (exchange);


--
-- Name: canonical_signals_message_revision; Type: INDEX; Schema: public; Owner: fatty_app
--

CREATE UNIQUE INDEX canonical_signals_message_revision ON public.canonical_signals USING btree (message_id, revision);


--
-- Name: dispatch_transitions_dispatch_time; Type: INDEX; Schema: public; Owner: fatty_app
--

CREATE INDEX dispatch_transitions_dispatch_time ON public.dispatch_transitions USING btree (dispatch_id, created_at);


--
-- Name: fallback_protection_active; Type: INDEX; Schema: public; Owner: fatty_app
--

CREATE INDEX fallback_protection_active ON public.fallback_protection USING btree (exchange, symbol) WHERE (state = 'active'::text);


--
-- Name: fallback_protection_active_key; Type: INDEX; Schema: public; Owner: fatty_app
--

CREATE UNIQUE INDEX fallback_protection_active_key ON public.fallback_protection USING btree (position_key) WHERE ((state = ANY (ARRAY['active'::text, 'closing'::text])) AND (position_key IS NOT NULL));


--
-- Name: fills_intent_lookup; Type: INDEX; Schema: public; Owner: fatty_app
--

CREATE INDEX fills_intent_lookup ON public.fills USING btree (exchange, client_order_id);


--
-- Name: notifications_outbox_pending; Type: INDEX; Schema: public; Owner: fatty_app
--

CREATE INDEX notifications_outbox_pending ON public.notifications_outbox USING btree (created_at, id) WHERE ((sent_at IS NULL) AND (failed_at IS NULL));


--
-- Name: one_active_position_per_venue_symbol; Type: INDEX; Schema: public; Owner: fatty_app
--

CREATE UNIQUE INDEX one_active_position_per_venue_symbol ON public.positions USING btree (exchange, symbol) WHERE (closed_at IS NULL);


--
-- Name: position_snapshots_exchange_symbol_time; Type: INDEX; Schema: public; Owner: fatty_app
--

CREATE INDEX position_snapshots_exchange_symbol_time ON public.position_snapshots USING btree (exchange, symbol, captured_at);


--
-- Name: provider_reconciliation_events_symbol_time; Type: INDEX; Schema: public; Owner: fatty_app
--

CREATE INDEX provider_reconciliation_events_symbol_time ON public.provider_reconciliation_events USING btree (exchange, symbol, created_at);


--
-- Name: source_management_updates_claim; Type: INDEX; Schema: public; Owner: fatty_app
--

CREATE INDEX source_management_updates_claim ON public.source_management_updates USING btree (created_at, id) WHERE (state = 'queued'::text);


--
-- Name: canary_entry_reservations canary_entry_reservations_dispatch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.canary_entry_reservations
    ADD CONSTRAINT canary_entry_reservations_dispatch_id_fkey FOREIGN KEY (dispatch_id) REFERENCES public.dispatches(id);


--
-- Name: canonical_signals canonical_signals_message_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.canonical_signals
    ADD CONSTRAINT canonical_signals_message_id_fkey FOREIGN KEY (message_id) REFERENCES public.telegram_messages(id);


--
-- Name: dispatch_transitions dispatch_transitions_dispatch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.dispatch_transitions
    ADD CONSTRAINT dispatch_transitions_dispatch_id_fkey FOREIGN KEY (dispatch_id) REFERENCES public.dispatches(id);


--
-- Name: fills fills_exchange_client_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.fills
    ADD CONSTRAINT fills_exchange_client_order_id_fkey FOREIGN KEY (exchange, client_order_id) REFERENCES public.live_order_intents(exchange, client_order_id);


--
-- Name: orders orders_dispatch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_dispatch_id_fkey FOREIGN KEY (dispatch_id) REFERENCES public.dispatches(id);


--
-- Name: orders orders_position_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_position_id_fkey FOREIGN KEY (position_id) REFERENCES public.positions(id);


--
-- Name: protection_states protection_states_position_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.protection_states
    ADD CONSTRAINT protection_states_position_id_fkey FOREIGN KEY (position_id) REFERENCES public.positions(id);


--
-- Name: provider_reconciliation_events provider_reconciliation_events_exchange_client_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.provider_reconciliation_events
    ADD CONSTRAINT provider_reconciliation_events_exchange_client_order_id_fkey FOREIGN KEY (exchange, client_order_id) REFERENCES public.live_order_intents(exchange, client_order_id);


--
-- Name: source_management_provider_intents source_management_provider_intents_management_update_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.source_management_provider_intents
    ADD CONSTRAINT source_management_provider_intents_management_update_id_fkey FOREIGN KEY (management_update_id) REFERENCES public.source_management_updates(id);


--
-- Name: source_management_updates source_management_updates_source_message_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: fatty_app
--

ALTER TABLE ONLY public.source_management_updates
    ADD CONSTRAINT source_management_updates_source_message_id_fkey FOREIGN KEY (source_message_id) REFERENCES public.telegram_messages(id);


--
-- PostgreSQL database dump complete
--

\unrestrict zVCWBORbwBLqBEQRhotKfh2VzeU7AwcbW4U0jFGvA4gvR32XgXxnsIBb63Nzb7T

