import type { RankedCustomer } from "./contracts";

interface RankedCustomersProps {
  customers: RankedCustomer[];
  selectedCustomerId: string | null;
  hasReport: boolean;
  onSelectCustomer: (customerId: string) => void;
}

const riskLabels = {
  high: "높은 위험",
  medium: "주의",
  low: "낮은 위험",
};

const seoulDate = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

export function RankedCustomers({
  customers,
  selectedCustomerId,
  hasReport,
  onSelectCustomer,
}: RankedCustomersProps) {
  return (
    <section className="panel ranking-panel" aria-labelledby="ranking-title">
      <div className="panel-heading">
        <div>
          <p className="section-kicker">04 · MATCHES</p>
          <h2 id="ranking-title">패턴 일치 고객</h2>
        </div>
        {hasReport ? <span className="count-chip">{customers.length}명</span> : null}
      </div>

      {!hasReport ? (
        <div className="panel-placeholder">
          <span aria-hidden="true">⌁</span>
          <p>분석을 완료하면 근거가 확인된 고객만 표시합니다.</p>
        </div>
      ) : customers.length === 0 ? (
        <div className="panel-placeholder zero-placeholder">
          <span aria-hidden="true">0</span>
          <p>현재 Source 범위에 완전한 Journey 패턴 일치 고객이 없습니다.</p>
        </div>
      ) : (
        <div className="table-scroll">
          <table aria-label="완전한 Journey 패턴 일치 고객">
            <caption>완전한 Journey 패턴 일치 고객</caption>
            <thead>
              <tr>
                <th scope="col">고객</th>
                <th scope="col">Risk</th>
                <th scope="col">확인 신호</th>
                <th scope="col">마지막 이벤트</th>
                <th scope="col">
                  <span className="sr-only">Journey 선택</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {customers.map((customer, index) => (
                <tr
                  className={
                    customer.customer_id === selectedCustomerId ? "is-selected" : ""
                  }
                  key={customer.customer_id}
                >
                  <td>
                    <span className="rank-index">{String(index + 1).padStart(2, "0")}</span>
                    <strong>{customer.customer_id}</strong>
                  </td>
                  <td>
                    <span className={`risk-pill risk-${customer.risk_level}`}>
                      {riskLabels[customer.risk_level]} · {customer.risk_score}
                    </span>
                  </td>
                  <td>
                    <div className="signal-tags">
                      {customer.signals.map((signal) => (
                        <span key={`${customer.customer_id}-${signal.code}`}>
                          {signal.label}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td>
                    {customer.last_event_at
                      ? seoulDate.format(new Date(customer.last_event_at))
                      : "—"}
                  </td>
                  <td>
                    <button
                      className="row-action"
                      type="button"
                      aria-label={`${customer.customer_id} Journey 보기`}
                      aria-pressed={customer.customer_id === selectedCustomerId}
                      onClick={() => onSelectCustomer(customer.customer_id)}
                    >
                      Journey 보기 <span aria-hidden="true">→</span>
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
