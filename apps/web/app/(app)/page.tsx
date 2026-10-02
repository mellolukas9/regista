export default function HomePage() {
  return (
    <>
      <header>
        <h1 className="text-display max-[899px]:text-[24px]">Início</h1>
        <p className="mt-1 max-w-[680px] text-body text-text-label">
          As áreas de operação aparecem aqui conforme forem liberadas.
        </p>
      </header>
      <section className="rounded-card border border-border bg-panel p-5">
        <h2 className="text-title">Nada por aqui ainda</h2>
        <p className="mt-1 text-body-sm text-text-label">
          Bots, execuções, filas e máquinas chegam nos próximos marcos.
        </p>
      </section>
    </>
  );
}
