import AppFooter from '../components/AppFooter';
import DocsSidebar from '../components/docs/DocsSidebar';
import DocumentationPage from '../components/docs/DocumentationPage';

export default function DocumentationView() {
  return (
    <div className="min-h-screen bg-paper text-ink lg:flex">
      <DocsSidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <DocumentationPage />
        <AppFooter />
      </div>
    </div>
  );
}
