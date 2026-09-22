---
layout: home

title: Niyān
titleTemplate: Git-based Data Version Control

hero:
  name: Niyān
  text: Data Version Control
  tagline: A self-hosted data forge built on Git, Git LFS, and S3-compatible storage for researchers and machine-learning teams.
  image:
    src: /hero-v3.png
    alt: A versioned dataset flowing through branches into object storage
  actions:
    - theme: brand
      text: Read the documentation
      link: /docs/
    - theme: alt
      text: View on GitHub
      link: https://github.com/niyan-project/niyan

features:
  - icon: ⎇
    title: Git-based
    details: Every dataset is an ordinary Git repository. Branches, tags, commits, and familiar tooling remain available.
  - icon: ↗
    title: S3 integration
    details: Upload, download, and stream data directly to and from S3-compatible storage for faster transfers and a smoother experience.
  - icon: ⌁
    title: Built for research
    details: Keep datasets reproducible alongside the code that consumes them, from an individual workstation to a shared lab installation.
  - icon: ◇
    title: Python Integration
    details: Stream remote files into Python workflows or download them explicitly without checking out an entire dataset.
  - icon: ⛭
    title: Self-hosted
    details: Own the control plane, Git repositories, metadata, and object storage. Deploy the complete stack with Docker Compose.
  - icon: ⟐
    title: Open by design
    details: Standard Git and Git LFS remain supported, while documented interfaces make datasets easier to access.
---

<section class="niyan-home-section niyan-workflow">
  <div>
    <h2>A familiar workflow for data</h2>
    <p>Niyān adds the coordination that large datasets need without hiding the tools underneath. Use the recommended CLI for the smoothest experience, or work with standard Git and Git LFS if you prefer.</p>
  </div>

  <div class="niyan-terminal" aria-label="Example Niyān workflow">
    <div class="niyan-terminal-bar" aria-hidden="true"><span></span><span></span><span></span></div>
    <pre><span class="prompt">$</span> pipx install niyan
<span class="prompt">$</span> niyan auth login https://data.example.org
<span class="prompt">$</span> niyan dataset clone lab/cell-images
<span class="prompt">$</span> cd cell-images
<span class="prompt">$</span> niyan status</pre>
  </div>
</section>

<section class="niyan-home-section">
  <h2>Reliable infrastructure for serious data</h2>
  <p>Niyān is built on top of a few reliable components: Git records history, Git LFS represents large files, S3-compatible storage holds their contents, and Niyān's custom control plane manages identity, access, and the web experience.</p>

  <div class="niyan-trust">
    <article>
      <h3>One source of truth</h3>
      <p>Pin an immutable dataset commit beside your research code and return to exactly the same inputs later.</p>
    </article>
    <article>
      <h3>No proprietary repository format</h3>
      <p>Clone, inspect, and recover datasets with established tools, even when the Niyān CLI is not available.</p>
    </article>
    <article>
      <h3>Designed to be operated</h3>
      <p>A focused control plane and a small number of durable services keep institutional deployment straightforward.</p>
    </article>
  </div>
</section>
