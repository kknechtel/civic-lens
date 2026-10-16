import type { Metadata } from "next";

// Template policy for a Civic Lens deployment. Operators: set
// NEXT_PUBLIC_APP_NAME, NEXT_PUBLIC_CONTACT_EMAIL and NEXT_PUBLIC_EFFECTIVE_DATE,
// and have counsel review this text for your jurisdiction before launch.
const APP = process.env.NEXT_PUBLIC_APP_NAME || "Civic Lens";

export const metadata: Metadata = {
  title: `Privacy Policy · ${APP}`,
  description: `How ${APP} collects, uses, and protects your information.`,
};

const EFFECTIVE = process.env.NEXT_PUBLIC_EFFECTIVE_DATE || "—";
const CONTACT = process.env.NEXT_PUBLIC_CONTACT_EMAIL || "privacy@example.org";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-8">
      <h2 className="text-lg font-semibold text-gray-900 mb-2">{title}</h2>
      <div className="space-y-3 text-sm text-gray-700 leading-relaxed">{children}</div>
    </section>
  );
}

export default function PrivacyPage() {
  return (
    <main className="min-h-screen bg-gray-50">
      <div className="max-w-3xl mx-auto px-4 sm:px-6 py-10 sm:py-14">
        <a href="/" className="text-xs text-gray-500 hover:text-gray-700">← {APP}</a>
        <h1 className="text-3xl font-semibold text-gray-900 mt-4">Privacy Policy</h1>
        <p className="text-sm text-gray-500 mt-1">Effective {EFFECTIVE}</p>

        <div className="mt-6 text-sm text-gray-700 leading-relaxed space-y-3">
          <p>
            This policy covers <strong>{APP}</strong>, a civic research site built on the
            open-source Civic Lens project (the &ldquo;Service&rdquo;). Unless the operator states
            otherwise, the Service is an independent project and is not operated by, or affiliated
            with, the governments whose public records it indexes.
          </p>
          <p>
            Most of what the Service shows is already public: meeting agendas, minutes and
            recordings, budgets, audits, ordinances, and other records published by the town and
            school district, plus public event listings. This policy explains the limited
            personal information we collect from people who use it.
          </p>
        </div>

        <Section title="Information we collect">
          <p><strong>Account information.</strong> When you create an account or sign in, we store
            your email address, username, and, if you set one, your name or display name. If you
            use a password, we store only a one-way hash of it. If you sign in with Google, we
            receive your Google account ID, name, email address, and profile photo URL from Google.
            We do not receive your Google password or access to your Google account data beyond
            that basic profile.</p>
          <p><strong>Content you add.</strong> Documents you upload, questions you ask the AI
            assistant (and its answers), saved searches and email alerts, presentations and
            comments, and event submissions.</p>
          <p><strong>Usage information.</strong> Search queries and which results you open, used to
            improve search quality; AI usage records (which feature ran and how many tokens it
            used), used to manage cost; the time of your last sign-in; and standard server logs
            (IP address, browser type, and requested URL), used for security and troubleshooting.</p>
          <p><strong>Stored on your device.</strong> We keep your sign-in token and a few display
            preferences (such as panel widths) in your browser&apos;s local storage. We do not use
            advertising cookies or third-party analytics or tracking tools.</p>
        </Section>

        <Section title="Public records and property data">
          <p>Property (parcel) information comes from New Jersey&apos;s public MOD-IV tax records.
            In keeping with New Jersey&apos;s Daniel&apos;s Law (P.L. 2020, c.125), we do not
            collect, store, display, or allow searching by property owner names.</p>
          <p>Public meeting recordings are transcribed and summarized automatically. Those
            transcripts can include the names and remarks of people who spoke at public meetings,
            as they appear in the official recording.</p>
        </Section>

        <Section title="How we use information">
          <ul className="list-disc pl-5 space-y-1.5">
            <li>To sign you in and keep your account secure.</li>
            <li>To provide the features you use: search, document viewing, AI answers, and alerts.</li>
            <li>To send the email digests you subscribe to. You can turn these off at any time on
              the Alerts page.</li>
            <li>To moderate community content and event submissions.</li>
            <li>To fix problems, prevent abuse, and improve the Service.</li>
          </ul>
          <p>We do not sell your personal information, and we do not use it for advertising.</p>
        </Section>

        <Section title="What other people can see">
          <p>Comments on shared
            presentations show your name to people the presentation is shared with. Documents you
            upload are private to your projects unless you share them.</p>
        </Section>

        <Section title="Service providers">
          <p>We use the following providers to run the Service. They process data only to provide
            their service to us.</p>
          <ul className="list-disc pl-5 space-y-1.5">
            <li><strong>Amazon Web Services</strong>: hosting, database, file storage, and email
              delivery (Amazon SES).</li>
            <li><strong>Google</strong>: Google Sign-In, and Gemini AI for document reading,
              summaries, and search.</li>
            <li><strong>Anthropic</strong>: Claude AI for the assistant, summaries, and analysis.</li>
            <li><strong>OpenAI</strong>: Whisper, for transcribing public meeting recordings.</li>
            <li><strong>Voyage AI</strong>: text embeddings that power semantic search.</li>
          </ul>
          <p>When you ask the AI assistant a question, your question and the relevant document
            excerpts are sent to the AI provider to generate an answer. Documents you upload may
            likewise be sent to an AI provider for text extraction and summarization.</p>
        </Section>

        <Section title="Google user data">
          <p>The Service uses Google Sign-In only to authenticate you. We use your basic Google
            profile (name, email address, profile photo) to create and identify your account, and
            for nothing else. We do not request access to Gmail, Drive, Calendar, or any other
            Google data. We do not share Google user data with third parties except as needed to
            operate the Service, and we do not use it for advertising. Our use of information
            received from Google APIs follows the{" "}
            <a className="underline" href="https://developers.google.com/terms/api-services-user-data-policy"
              target="_blank" rel="noopener noreferrer">Google API Services User Data Policy</a>,
            including the Limited Use requirements.</p>
        </Section>

        <Section title="Retention and deletion">
          <p>We keep account information and content you add for as long as your account exists.
            To delete your account, or to get a copy of your data, email{" "}
            <a className="underline" href={`mailto:${CONTACT}`}>{CONTACT}</a>. When we delete an
            account, we also delete your saved alerts and other content tied to it.
            Search and usage logs are kept with your account link removed. Server logs are kept
            only briefly, for operations.</p>
        </Section>

        <Section title="Security">
          <p>All traffic is encrypted with HTTPS. Passwords are hashed, files are kept in private
            storage and served through short-lived signed links, and access to each project&apos;s
            documents is checked on every request. No system is perfectly secure, but we work to
            protect your information.</p>
        </Section>

        <Section title="Children">
          <p>The Service is not directed to children under 13, and we do not knowingly collect
            personal information from them.</p>
        </Section>

        <Section title="Changes">
          <p>If we change this policy, we will update the effective date above. If a change is
            significant, we will also notify signed-in users.</p>
        </Section>

        <Section title="Contact">
          <p>Questions or requests: <a className="underline" href={`mailto:${CONTACT}`}>{CONTACT}</a>.</p>
        </Section>
      </div>
    </main>
  );
}
