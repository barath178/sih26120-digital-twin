import { go } from "../router";
import { Tabs } from "../components/ui";
import DataUpload from "./DataUpload";
import Models from "./Models";
import About from "./About";

type Tab = "replay" | "models" | "assumptions";

/** Evidence page: bring data in, see how far the models can be trusted, and see where every number came from. */
export default function DataModels({ tab }: { tab: Tab }) {
  return (
    <div className="space-y-5">
      <Tabs label="Data and models" value={tab} onChange={(k) => go({ page: "data", tab: k })} tabs={[
        { key: "replay", label: "Data upload & replay", hint: "validate a CSV, replay it through the twin" },
        { key: "models", label: "Model validation & calibration", hint: "accuracy on held-out synthetic data" },
        { key: "assumptions", label: "Assumptions & sources", hint: "provenance of every parameter" },
      ]} />
      {tab === "replay" ? <DataUpload /> : tab === "models" ? <Models /> : <About />}
    </div>
  );
}
