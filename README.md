# Floww backend

## User management

The backend uses WorkOS for authentication with JWT tokens. Users authenticate via Bearer tokens in the Authorization header, which are validated using WorkOS public keys. If a user doesn't exist in the database, they are automatically created along with a default namespace/workspace. The JWT contains the WorkOS user ID (`sub` claim) which serves as the primary identifier for user lookup and creation.

## WebSocket Integration

The backend uses Centrifugo v6 for real-time WebSocket communication with workflow channels. Each workflow gets its own isolated channel (`workflow:{workflow_id}`) with JWT-based authentication to ensure only authorized users can subscribe.

**Required Environment Variables:**
- `CENTRIFUGO_HOST`: Centrifugo server host (default: localhost)
- `CENTRIFUGO_PORT`: Centrifugo server port (default: 5001)
- `CENTRIFUGO_API_KEY`: Random string for backend-to-Centrifugo HTTP API authentication
- `CENTRIFUGO_JWT_SECRET`: Random string (32+ chars) for HMAC signing of client JWT tokens

**API Flow:**
1. Client tries to open connection to centrifugo
2. Authorization header is send to backend at `/centrifugo/connect` and connection is granted if JWT is valid
3. Subscriptions are requested to `workflow:{workflow_id}` and `/centrifugo/subscribe` is called to see if user should be able to do so.
4. Backend publishes workflow events (webhook received, execution started/completed/failed) to channels via HTTP API
5. Subscribed clients receive real-time updates for their authorized workflows

**Dashboard**

The centrifugo dashboard is proxies through the backend such that it can benefit from the same authentication.
It is available at `/admin/centrifugo`.


## Admin Interface

The admin interface is available at `/admin` and uses WorkOS OAuth for authentication.

**Authentication Flow:**
- Unauthenticated users are redirected to `/auth/login` with `?next=` parameter
- OAuth flow: `/auth/login` → WorkOS → `/auth/callback` → redirect to original page
- Admin access requires user id to be in the approved admin list (checked via JWT)
- Non-admin users receive 403 response even with valid authentication