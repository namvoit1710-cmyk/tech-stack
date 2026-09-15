-- Migration: store the raw inbound body on each webhook delivery (SA-2059 B9).
--
-- The FE Deliveries tab lets you click a row to see the exact payload that
-- arrived — the fastest way to debug a signature/topic mismatch. NCLOB because
-- provider payloads (Jira issue events) run tens of KB, and the ingress caps at 64KB
-- and the log stays bounded to 100 rows/registration.
ALTER TABLE "AIW_WEBHOOK_DELIVERIES" ADD ("PAYLOAD" NCLOB DEFAULT NULL)
