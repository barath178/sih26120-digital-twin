import { go } from "../router";
import { Tabs } from "../components/ui";
import Optimizer from "./Optimizer";
import WhatIf from "./WhatIf";

/** One page, two tools on the same well: search for the best coupled plan, or hand-test a change. */
export default function Optimize({ id, tab }: { id: string; tab: "optimize" | "whatif" }) {
  return (
    <div className="space-y-5">
      <Tabs label="Optimisation tools" value={tab} onChange={(k) => go({ page: "optimize", id, tab: k })} tabs={[
        { key: "optimize", label: "Optimise CSS + SRP together", hint: "numerical search under your constraints" },
        { key: "whatif", label: "What-if simulator", hint: "test up to 3 scenarios against the current plan" },
      ]} />
      {tab === "optimize" ? <Optimizer id={id} /> : <WhatIf id={id} />}
    </div>
  );
}
