import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { LogIn } from "lucide-react";
import NetraLogo from "../components/NetraLogo.jsx";
import ThemeToggle from "../components/ui/ThemeToggle.jsx";
import Button from "../components/ui/Button.jsx";
import { Card, CardBody } from "../components/ui/Card.jsx";
import Field, { Input } from "../components/ui/Field.jsx";
import { login } from "../lib/auth.js";

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const from = location.state?.from?.pathname || "/app";

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(err.status === 401 ? "Incorrect email or password." : err.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-dvh flex-col items-center justify-center bg-bg px-4 py-10">
      <div className="absolute right-4 top-4">
        <ThemeToggle />
      </div>

      <div className="mb-6">
        <NetraLogo size={44} tagline />
      </div>

      <Card className="w-full max-w-sm">
        <CardBody className="flex flex-col gap-5">
          <div>
            <h1 className="text-lg font-medium text-ink">Demo access</h1>
            <p className="mt-1 text-[13px] text-ink-3">
              Sign in with the demo credentials to view the registry, live feeds, and watchlist.
            </p>
          </div>

          <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
            <Field label="Email" required>
              {(inputProps) => (
                <Input
                  {...inputProps}
                  type="email"
                  autoComplete="username"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                />
              )}
            </Field>

            <Field label="Password" required>
              {(inputProps) => (
                <Input
                  {...inputProps}
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                />
              )}
            </Field>

            {error && (
              <p role="alert" className="text-[13px] font-medium text-danger">
                {error}
              </p>
            )}

            <Button type="submit" loading={submitting} className="mt-1">
              <LogIn className="h-4 w-4" aria-hidden="true" />
              Sign in
            </Button>
          </form>
        </CardBody>
      </Card>
    </div>
  );
}
