import { Link } from "react-router-dom";
import Button from "../components/ui/Button.jsx";
import NetraLogo from "../components/NetraLogo.jsx";

export default function NotFound() {
  return (
    <div className="grid min-h-dvh place-items-center bg-bg px-6">
      <div className="text-center">
        <NetraLogo size={48} />
        <h1 className="mt-6 font-display text-3xl font-semibold text-ink">Nothing here</h1>
        <p className="mx-auto mt-2 max-w-sm text-[15px] leading-relaxed text-ink-3">
          That page isn&apos;t part of Netra. The control room has everything.
        </p>
        <div className="mt-7 flex flex-wrap justify-center gap-3">
          <Link to="/app">
            <Button>Open the control room</Button>
          </Link>
          <Link to="/">
            <Button variant="secondary">Back to overview</Button>
          </Link>
        </div>
      </div>
    </div>
  );
}
