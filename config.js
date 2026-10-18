// Public Supabase settings for the browser. Both values are SAFE to commit:
// the anon/publishable key only identifies the project; what a visitor may do is
// enforced inside the database (sign-in checks, roles, row level security).
// NEVER put the service_role key or the database password here.
//
// Find them in Supabase: Project Settings -> API (Project URL + anon public key).
window.QUIZ_CONFIG = {
  SUPABASE_URL: "https://YOUR-PROJECT-REF.supabase.co",
  SUPABASE_ANON_KEY: "YOUR-ANON-PUBLIC-KEY",
};
