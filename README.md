# LifeOS - WhatsApp AI Assistant

## Deployment Guide (Vercel)

### 1. Prerequisites
- GitHub Account
- Vercel Account
- Supabase Project

### 2. Environment Variables
You must set these in your **Vercel Project Settings > Environment Variables**:

- `GEMINI_API_KEY`: Your Google Gemini API Key.
- `FONNTE_TOKEN`: Your Fonnte Token.
- `SUPABASE_URL`: Your Supabase Project URL (e.g., `https://xyz.supabase.co`).
- `SUPABASE_KEY`: Your Supabase Service Role Key (for full database access).

### 3. Deployment
1. Push this code to GitHub.
2. Import the repository in Vercel.
3. Vercel will automatically detect the Python app (configured in `vercel.json`).
4. Set the Environment Variables.
5. Deploy!

### 5. Connect Fonnte
After deployment, get your Vercel URL (e.g., `https://lifeos.vercel.app`).
In Fonnte Dashboard, set the Webhook URL to:
`https://lifeos.vercel.app/webhook`
