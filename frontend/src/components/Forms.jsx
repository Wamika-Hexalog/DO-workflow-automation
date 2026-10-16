import Field from "./Field.jsx";
import {
  CheckIcon,
  FileIcon,
  ListCheckIcon,
  PenIcon,
  ReceiptIcon,
  ShipIcon,
  TruckIcon,
  UserIcon,
  UsersIcon,
  WalletIcon,
} from "./Icons.jsx";

const GROUP_ICON = {
  "Bill of lading": FileIcon,
  Parties: UsersIcon,
  Letters: PenIcon,
  "CMA CGM checklist": ListCheckIcon,
  "Liner invoice": ReceiptIcon,
  "Payment and deposit": WalletIcon,
};

// The fixed details come as one list; these groups only arrange them. A key not
// listed lands in "Other details", so a new field in do_folder.yaml still shows.
const FIXED_GROUPS = [
  { title: "Forwarder and signatory", icon: UserIcon, keys: ["forwarder_name", "forwarder_signatory", "forwarder_designation", "forwarder_pan"] },
  { title: "Consignee and CHA contacts", icon: UsersIcon, keys: ["forwarder_tel", "forwarder_email", "cha_email"] },
  { title: "DO collection", icon: TruckIcon, keys: ["do_collector", "odex_runner_boy", "odex_runner_mobile"] },
  {
    title: "Shipping line and ODeX",
    icon: ShipIcon,
    keys: ["line_agent", "line_agent_address", "odex_liner", "odex_hss_customer", "factory_address", "factory_pin", "odex_do_remarks"],
  },
  { title: "Payments to CMA CGM", icon: WalletIcon, keys: ["payer_bank", "payer_account_no", "rtgs_contact_name", "rtgs_contact_number", "do_mailbox"] },
];

function Group({ title, icon: GroupIcon = FileIcon, fields, status, children }) {
  const required = fields.filter((f) => !f.optional);
  const done = required.filter((f) => status[f.key] === "ok").length;
  return (
    <div className="panel">
      <h4 className="group-title">
        <span className="group-icon">
          <GroupIcon size={16} />
        </span>
        <span className="group-name">{title}</span>
        {required.length > 0 &&
          (done === required.length ? (
            <span className="group-state done">
              <CheckIcon size={13} />
              Complete
            </span>
          ) : (
            <span className="group-state">
              {done} of {required.length} filled
            </span>
          ))}
      </h4>
      <div className="grid">{children}</div>
    </div>
  );
}

export function ShipmentForm({ folder, values, setValue, issues, pendingKeys, status }) {
  return (
    <section>
      <h3 className="section-title">Shipment details</h3>
      {folder.shipment_groups.map((group) => (
        <Group key={group.title} title={group.title} icon={GROUP_ICON[group.title]} fields={group.fields} status={status}>
          {group.fields.map((f) => (
            <Field
              key={f.key}
              field={f}
              value={values[f.key]}
              uses={folder.useCount[f.key]}
              issues={issues[f.key]}
              pending={pendingKeys.has(f.key)}
              onChange={(value) => setValue(f.key, value)}
            />
          ))}
        </Group>
      ))}
    </section>
  );
}

export function FixedForm({ folder, values, setValue, status }) {
  const listed = new Set(FIXED_GROUPS.flatMap((g) => g.keys));
  const byKey = Object.fromEntries(folder.profile_fields.map((f) => [f.key, f]));
  const groups = [
    ...FIXED_GROUPS.map((g) => ({ ...g, fields: g.keys.map((k) => byKey[k]).filter(Boolean) })),
    { title: "Other details", fields: folder.profile_fields.filter((f) => !listed.has(f.key)) },
  ].filter((g) => g.fields.length);
  return (
    <section>
      <h3 className="section-title">Fixed details</h3>
      {groups.map((group) => (
        <Group key={group.title} title={group.title} icon={group.icon} fields={group.fields} status={status}>
          {group.fields.map((f) => (
            <Field
              key={f.key}
              field={f}
              value={values[f.key]}
              uses={folder.useCount[f.key]}
              onChange={(value) => setValue(f.key, value)}
            />
          ))}
        </Group>
      ))}
    </section>
  );
}
