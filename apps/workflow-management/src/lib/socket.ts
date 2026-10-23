import { env } from "@/env";
import { SocketClient } from "@ldc/api-sdk/socket";

export const pushGatewaySocket = new SocketClient({
    baseUrl: `${env.PUBLIC_URL_PUSH_GATEWAY}`,
    timeout: 20000,
    reconnection: true,
    reconnectionAttempts: 10,
    reconnectionDelay: 1000,
})