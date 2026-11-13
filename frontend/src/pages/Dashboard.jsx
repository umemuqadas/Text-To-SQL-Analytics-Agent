import ChatInterface from '../components/ChatInterface'

export default function Dashboard({ conversationId }) {
  return (
    <section className="flex h-full flex-col">
      <ChatInterface conversationId={conversationId} />
    </section>
  )
}
