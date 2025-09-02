import { BrowserRouter, Routes, Route, Link } from "react-router-dom";
import OAuth from "./pages/OAuth";
import RespondMass from "./pages/RespondMass";
import Search from "./pages/Search";

export default function App() {
  return (
    <BrowserRouter>
      <div className="px-4 py-2 border-b flex gap-4 sticky top-0 bg-white">
        <Link to="/">Home</Link>
        <Link to="/oauth">OAuth</Link>
        <Link to="/search">Search</Link>
        <Link to="/respond-mass">RespondMass</Link>
      </div>
      <div className="p-4 bg-blue-500 text-white">Tailwind работает!</div>
      <Routes>
        <Route path="/" element={<div className="p-6">Welcome to hhcli Web</div>} />
        <Route path="/oauth" element={<OAuth />} />
        <Route path="/search" element={<Search />} />
        <Route path="/respond-mass" element={<RespondMass />} />
      </Routes>
    </BrowserRouter>
  );
}
