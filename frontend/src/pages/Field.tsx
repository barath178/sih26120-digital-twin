import { go, type FieldTab } from "../router";
import { PageHeader, Tabs } from "../components/ui";
import FieldOverview from "./FieldOverview";
import FieldPlan from "./FieldPlan";
import Maintenance from "./Maintenance";

/** Field level: today's picture, the steam-constrained field plan, and the reliability/rig plan. */
export default function Field({ tab }: { tab: FieldTab }) {
  return (
    <div className="space-y-5">
      <PageHeader eyebrow="Field" title="Ten wells, two steam generators" subtitle="See the whole field, plan steam across it, and schedule the rigs the twin says will be needed." />
      <Tabs label="Field views" value={tab} onChange={(k) => go({ page: "field", tab: k })} tabs={[
        { key: "overview", label: "Overview & steam", hint: "map, wells, generator calendar" },
        { key: "plan", label: "Field optimiser", hint: "all wells under one steam budget" },
        { key: "maintenance", label: "Maintenance planner", hint: "predicted failures, rig schedule" },
      ]} />
      {tab === "overview" ? <FieldOverview /> : tab === "plan" ? <FieldPlan /> : <Maintenance />}
    </div>
  );
}
