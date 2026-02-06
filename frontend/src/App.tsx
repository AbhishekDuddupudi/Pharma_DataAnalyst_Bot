import { Routes, Route } from "react-router-dom";

import RequireAuth from "./auth/RequireAuth";
import AppLayout from "./layouts/AppLayout";
import Chat from "./pages/Chat";
import Login from "./pages/Login";

export default function App() {
  return (
    <Routes>
      <Route
        element={
          <RequireAuth>
            <AppLayout />
          </RequireAuth>
        }
      >
        <Route index element={<Chat />} />
      </Route>
      <Route path="/login" element={<Login />} />
    </Routes>
  );
}
