import {
  ArrowRight,
  AudioLines,
  Check,
  Headphones,
  MessageSquareText,
  ShieldCheck,
  Waypoints,
} from "lucide-react";
import { Brand, ButtonLink, TextLink } from "@/components/ui";

export default function Landing() {
  return (
    <div className="landing">
      <header className="public-header">
        <Brand />
        <nav aria-label="Public navigation">
          <a href="#how-it-works" className="desktop-only">
            How it works
          </a>
          <ButtonLink href="/sign-in" secondary>
            Sign in
          </ButtonLink>
        </nav>
      </header>
      <main id="main">
        <section className="hero">
          <div className="hero-copy">
            <p className="eyebrow">
              <span className="live-dot" /> YOUR LISTENING. OUR FOCUS.
            </p>
            <h1>
              A little clarity.
              <br />
              Back to <span>listening.</span>
            </h1>
            <p className="hero-description">
              When something interrupts the music, let’s work through it. One
              place for your Spotify questions, conversations, and next steps.
            </p>
            <div className="hero-actions">
              <ButtonLink href="/sign-up">
                Get support <ArrowRight size={18} aria-hidden="true" />
              </ButtonLink>
              <TextLink href="/sign-in">Track a request</TextLink>
            </div>
            <p className="hero-note">
              <ShieldCheck size={16} aria-hidden="true" /> Your questions. A
              real conversation. Clear updates.
            </p>
          </div>
          <div
            className="listening-art"
            aria-label="Abstract sound waves coming into focus"
            role="img"
          >
            <div className="art-grid" />
            <div className="art-orbit orbit-one" />
            <div className="art-orbit orbit-two" />
            <div className="art-label">
              <span className="live-dot" /> LESS FRICTION. MORE FLOW.
            </div>
            <div className="sound-lens">
              <AudioLines strokeWidth={1.3} size={128} aria-hidden="true" />
            </div>
            <span className="art-spark spark-one">+</span>
            <span className="art-spark spark-two">+</span>
            <div className="art-footer">
              <Headphones size={20} aria-hidden="true" />
              <span>
                Good support starts
                <br />
                <strong>with listening.</strong>
              </span>
            </div>
            <span className="art-index" aria-hidden="true">
              SL / 01
            </span>
          </div>
        </section>
        <section className="support-strip" aria-label="Support topics">
          <span>Here for the moments between the music.</span>
          <div>
            <span>
              <Check size={15} aria-hidden="true" /> Playback & app issues
            </span>
            <span>
              <Check size={15} aria-hidden="true" /> Account questions
            </span>
            <span>
              <Check size={15} aria-hidden="true" /> Subscription concerns
            </span>
          </div>
        </section>
        <section id="how-it-works" className="how-section">
          <div className="section-intro">
            <p className="eyebrow">A CLEAR PATH FORWARD</p>
            <h2>
              Support with
              <br />a little more context.
            </h2>
            <p>
              No scattered conversations. Keep your request and its progress in
              one place.
            </p>
          </div>
          <div className="steps">
            {[
              {
                n: "01",
                icon: MessageSquareText,
                title: "Tell us what’s happening",
                body: "Describe the issue in your own words. A little context helps an agent understand what you need.",
              },
              {
                n: "02",
                icon: Headphones,
                title: "Keep the conversation going",
                body: "Reply, add details, and read public responses in a conversation dedicated to your request.",
              },
              {
                n: "03",
                icon: Waypoints,
                title: "Know where things stand",
                body: "Follow status updates. If a resolved issue returns, reopen the request and continue from there.",
              },
            ].map(({ n, icon: Icon, title, body }) => (
              <article className="step" key={n}>
                <span className="step-number">{n}</span>
                <Icon size={25} strokeWidth={1.5} aria-hidden="true" />
                <div>
                  <h3>{title}</h3>
                  <p>{body}</p>
                </div>
              </article>
            ))}
          </div>
        </section>
        <section className="landing-cta">
          <div>
            <p className="eyebrow">LET’S GET IT SORTED</p>
            <h2>Your next step starts here.</h2>
          </div>
          <ButtonLink href="/sign-up">
            Create your account <ArrowRight size={18} aria-hidden="true" />
          </ButtonLink>
        </section>
      </main>
      <footer className="public-footer">
        <Brand />
        <p>
          Independent support for Spotify-focused questions.
          <br />
          SupportLens is not affiliated with or endorsed by Spotify.
        </p>
        <TextLink href="/sign-in">Agent sign in</TextLink>
      </footer>
    </div>
  );
}
