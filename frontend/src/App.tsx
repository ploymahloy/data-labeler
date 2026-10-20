import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { Layout } from "./components/Layout";
import { AuthCallbackPage } from "./pages/AuthCallbackPage";
import { BatchDetailPage } from "./pages/BatchDetailPage";
import { BatchesPage } from "./pages/BatchesPage";
import { LabelPage } from "./pages/LabelPage";
import { LoginPage } from "./pages/LoginPage";
import { ReviewPage } from "./pages/ReviewPage";
import { UploadPage } from "./pages/UploadPage";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

export const App = () => (
  <QueryClientProvider client={queryClient}>
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/auth/callback" element={<AuthCallbackPage />} />
          <Route element={<Layout />}>
            <Route path="/" element={<BatchesPage />} />
            <Route path="/batches/:batchId" element={<BatchDetailPage />} />
            <Route path="/batches/:batchId/upload" element={<UploadPage />} />
            <Route path="/batches/:batchId/label" element={<LabelPage />} />
            <Route path="/batches/:batchId/review" element={<ReviewPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  </QueryClientProvider>
);
