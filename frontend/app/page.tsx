import { Header } from "@/components/Header";
import { TabsContainer } from "@/components/TabsContainer";

export default function Home() {
  return (
    <>
      <Header />
      <main className="flex-1 max-w-6xl mx-auto w-full p-6">
        <h1 className="text-2xl font-bold mb-1">
          📦 <span className="text-orange-500">C</span>hat
          <span className="text-orange-500">2O</span>rder
        </h1>
        <p className="text-gray-600 mb-6">
          사장님은 소통에만 집중하세요. 대화 속 주문 정리는 C2O가 알아서 엑셀로 만들어 드립니다.
        </p>
        <TabsContainer />
      </main>
    </>
  );
}
